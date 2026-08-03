"""The control plane, held to the same contract as the fake.

`test_approvals.py` pins what a grant is allowed to authorise, against an in-process `FakeApprover`.
That fake is only worth something if the real thing behaves the same way, so the important tests
here are the ones that **re-run those properties against the real HTTP server** — the actual SQLite
store, the actual endpoints, the actual client the gate uses.

If these and the fake ever disagree, the fake is not a reference, it is a fiction.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import pytest

warnings.filterwarnings("ignore", message=".*starlette.testclient.*")

from fastapi.testclient import TestClient  # noqa: E402

from neti.approvals import Approval, ApprovalState  # noqa: E402
from neti.cloud import Credentials, HttpApprover, load_credentials, save_credentials  # noqa: E402
from neti.config.policy import Policy, load_policy  # noqa: E402
from neti.core.types import ProposedCall  # noqa: E402
from neti.core.verdict import Mode  # noqa: E402
from neti.engine import Engine  # noqa: E402
from neti.eval.synthetic import SyntheticTenant, default_tenant  # noqa: E402
from neti.gatekeeper import Gatekeeper  # noqa: E402
from neti.resolvers.graph_client import ClientCredential, GraphClient  # noqa: E402
from neti.resolvers.registry import resolvers_for_client  # noqa: E402
from neti_cloud.notify import summarise  # noqa: E402
from neti_cloud.server import create_app  # noqa: E402
from neti_cloud.store import Store  # noqa: E402
from tests.integration.test_inventory import EXAMPLE  # noqa: E402

KEY = "org-key-for-tests"
CRED = ClientCredential(tenant_id="d", client_id="d", client_secret="d")

CONFIRMING = ProposedCall(tool="send_email", args={"to": "g-dept"})
BLOCKING = ProposedCall(tool="remove_group_members", args={"group": "g-eng-all"})
FITTING = ProposedCall(tool="send_email", args={"to": "g-team"})


class ClientApprover(HttpApprover):
    """`HttpApprover` driven through a `TestClient` instead of a socket.

    Everything above the transport is the shipped code — the same request bodies, the same parsing,
    the same error mapping. Only the pipe is short-circuited.
    """

    def __init__(self, client: TestClient, wait_s: float = 0.0) -> None:
        self.url, self.key, self.wait_s, self.timeout_s = "", KEY, wait_s, 10.0
        self._client = client  # type: ignore[assignment]


@pytest.fixture
def store(tmp_path: Path) -> Any:
    db = Store(tmp_path / "cloud.db")
    yield db
    db.close()


@pytest.fixture
def api(store: Store) -> Any:
    with TestClient(create_app(store, org_key=KEY)) as client:
        client.headers.update({"Authorization": f"Bearer {KEY}"})
        yield client


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


def keeper(tenant: SyntheticTenant, approver: Any = None) -> Gatekeeper:
    policy: Policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.ENFORCE})
    client = GraphClient(CRED, transport=tenant.transport())
    engine = Engine(policy=policy, resolvers=resolvers_for_client(client))
    return Gatekeeper(engine=engine, approver=approver)


def pending_id(api: TestClient) -> str:
    rows = api.get("/v1/approvals", params={"state": "pending"}).json()["approvals"]
    return str(rows[0]["id"])


# ---------------------------------------------------------------------------- auth


def test_the_key_is_required(store: Store) -> None:
    with TestClient(create_app(store, org_key=KEY)) as bare:
        assert bare.get("/v1/approvals").status_code == 401
        assert bare.post("/v1/approvals", json={"digest": "x"}).status_code == 401
        # Health stays open so `neti login` can tell a wrong key from a wrong URL.
        assert bare.get("/v1/health").json()["ok"] is True


def test_a_wrong_key_is_rejected(store: Store) -> None:
    with TestClient(create_app(store, org_key=KEY)) as bare:
        bare.headers.update({"Authorization": "Bearer not-the-key"})
        assert bare.get("/v1/approvals").status_code == 401


# ---------------------------------------------------------------------------- the demo loop


def test_a_confirm_becomes_an_inbox_item_a_human_can_answer(
    api: TestClient, tenant: SyntheticTenant
) -> None:
    """The whole paid story in one test: stopped, asked, approved, proceeds."""
    approver = ClientApprover(api)
    gate = keeper(tenant, approver)

    first = gate.decide(CONFIRMING)
    assert not first.proceeds
    assert first.escalation.state is ApprovalState.PENDING

    inbox = api.get("/v1/approvals", params={"state": "pending"}).json()["approvals"]
    assert len(inbox) == 1
    # The reviewer is shown the magnitude, which is the entire reason they are being asked.
    assert inbox[0]["approved_magnitude"] == 500
    assert inbox[0]["unit"] == "recipients"

    api.post(
        f"/v1/approvals/{inbox[0]['id']}/decide",
        json={"granted": True, "decided_by": "sam@acme.com"},
    ).raise_for_status()

    second = gate.decide(CONFIRMING)
    assert second.proceeds
    assert second.escalation.approval is not None
    assert second.escalation.approval.decided_by == "sam@acme.com"


def test_a_denial_stops_the_call_and_names_the_denier(
    api: TestClient, tenant: SyntheticTenant
) -> None:
    gate = keeper(tenant, ClientApprover(api))
    gate.decide(CONFIRMING)
    api.post(
        f"/v1/approvals/{pending_id(api)}/decide",
        json={"granted": False, "decided_by": "alex@acme.com", "reason": "too broad"},
    ).raise_for_status()

    decision = gate.decide(CONFIRMING)
    assert not decision.proceeds
    assert decision.escalation.state is ApprovalState.DENIED
    assert decision.escalation.approval is not None
    assert decision.escalation.approval.decided_by == "alex@acme.com"


# ---------------------------------------------------------------------------- the four bindings


def test_a_grant_is_single_use_against_the_real_store(
    api: TestClient, tenant: SyntheticTenant
) -> None:
    gate = keeper(tenant, ClientApprover(api))
    gate.decide(CONFIRMING)
    api.post(
        f"/v1/approvals/{pending_id(api)}/decide", json={"granted": True, "decided_by": "sam"}
    ).raise_for_status()

    assert gate.decide(CONFIRMING).proceeds
    spent = gate.decide(CONFIRMING)
    assert not spent.proceeds


def test_redemption_is_atomic(api: TestClient, store: Store) -> None:
    """Two agents racing one grant. The UPDATE is the arbiter, not the read before it."""
    row = store.request("d1", {"tool": "send_email"}, 500, "recipients")
    store.decide(row.id, granted=True, decided_by="sam")

    outcomes = [store.redeem(row.id, 500).state for _ in range(5)]
    assert outcomes.count("granted") == 1
    assert set(outcomes[1:]) == {"expired"}


def test_a_grant_refuses_a_target_that_grew(api: TestClient, store: Store) -> None:
    """Approve 40 at 17:00; the group is nested into overnight; the grant must not cover 40,000."""
    row = store.request("d2", {"tool": "remove_group_members"}, 40, "principals")
    store.decide(row.id, granted=True, decided_by="sam")

    refused = store.redeem(row.id, 40_000)
    assert refused.state == "expired"
    assert refused.reason == "target grew past the approved magnitude"


def test_a_grant_for_one_call_does_not_cover_another(
    api: TestClient, tenant: SyntheticTenant
) -> None:
    """The binding that stops "approve the small one, execute the big one"."""
    gate = keeper(tenant, ClientApprover(api))
    gate.decide(CONFIRMING)
    api.post(
        f"/v1/approvals/{pending_id(api)}/decide", json={"granted": True, "decided_by": "sam"}
    ).raise_for_status()

    other = gate.decide(ProposedCall(tool="delete_group", args={"group": "g-dept"}))
    assert not other.proceeds


def test_a_retry_does_not_re_notify(api: TestClient, tenant: SyntheticTenant) -> None:
    gate = keeper(tenant, ClientApprover(api))
    for _ in range(3):
        gate.decide(CONFIRMING)
    assert len(api.get("/v1/approvals").json()["approvals"]) == 1


def test_an_expired_request_is_not_answerable(store: Store) -> None:
    lapsed = Store(":memory:", ttl_s=-1)
    try:
        row = lapsed.request("d3", {"tool": "send_email"}, 500, "recipients")
        assert lapsed.get(row.id) is not None
        assert lapsed.get(row.id).state == "expired"  # type: ignore[union-attr]
        assert lapsed.decide(row.id, granted=True, decided_by="sam") is None
    finally:
        lapsed.close()


def test_deciding_twice_is_a_conflict_not_a_silent_overwrite(api: TestClient) -> None:
    """A second reviewer must not be able to overturn the first without noticing."""
    created = api.post(
        "/v1/approvals", json={"digest": "d4", "magnitude": 10, "unit": "rows", "wait_s": 0}
    ).json()
    ok = api.post(
        f"/v1/approvals/{created['id']}/decide", json={"granted": True, "decided_by": "sam"}
    )
    assert ok.status_code == 200
    clash = api.post(
        f"/v1/approvals/{created['id']}/decide", json={"granted": False, "decided_by": "alex"}
    )
    assert clash.status_code == 409


# ---------------------------------------------------------------------------- what is never asked


def test_a_block_never_reaches_the_control_plane(api: TestClient, tenant: SyntheticTenant) -> None:
    keeper(tenant, ClientApprover(api)).decide(BLOCKING)
    assert api.get("/v1/approvals").json()["approvals"] == []


def test_a_call_that_fits_never_reaches_the_control_plane(
    api: TestClient, tenant: SyntheticTenant
) -> None:
    assert keeper(tenant, ClientApprover(api)).decide(FITTING).proceeds
    assert api.get("/v1/approvals").json()["approvals"] == []


# ---------------------------------------------------------------------------- the tier boundary


def test_an_unreachable_control_plane_behaves_like_the_free_tier(tenant: SyntheticTenant) -> None:
    """Nothing about paying may add availability risk to enforcement."""
    dead = HttpApprover(url="http://127.0.0.1:9", key=KEY, wait_s=0, timeout_s=0.2)
    try:
        free = keeper(tenant, None).decide(CONFIRMING)
        broken = keeper(tenant, dead).decide(CONFIRMING)
    finally:
        dead.close()

    assert broken.proceeds is free.proceeds is False
    assert broken.escalation.approval is None
    # Still legible in the record: nobody was asked, which is not the same as somebody saying no.
    assert "unreachable" in (broken.escalation.error or "")


def test_a_bad_key_is_an_outage_not_a_denial(api: TestClient, tenant: SyntheticTenant) -> None:
    """A rejected key must not read as a human declining the call."""
    api.headers.update({"Authorization": "Bearer wrong"})
    decision = keeper(tenant, ClientApprover(api)).decide(CONFIRMING)

    assert not decision.proceeds
    assert decision.escalation.approval is None
    assert "rejected the organisation key" in (decision.escalation.error or "")


# ---------------------------------------------------------------------------- odds and ends


def test_the_reviewers_sentence_leads_with_the_magnitude(store: Store) -> None:
    """ "Approve send_email?" is unanswerable. The number is what makes it a decision."""
    row = store.request(
        "d5",
        {"tool": "send_email", "ceiling": 50},
        500,
        "recipients",
    )
    what, _ = summarise(row)
    assert what == "send_email resolves to 500 recipients, above the declared ceiling of 50"


def test_an_unsizeable_target_says_so_rather_than_showing_a_number(store: Store) -> None:
    row = store.request("d6", {"tool": "send_email"}, None, "recipients")
    what, _ = summarise(row)
    assert "could not be sized" in what


def test_credentials_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETI_HOME", str(tmp_path))
    monkeypatch.delenv("NETI_CLOUD_URL", raising=False)
    monkeypatch.delenv("NETI_CLOUD_KEY", raising=False)

    path = save_credentials(Credentials(url="http://cp.internal", key="k", org="acme"))
    # The key can approve calls on the organisation's behalf; it is not world-readable.
    assert path.stat().st_mode & 0o077 == 0
    loaded = load_credentials()
    assert loaded is not None
    assert (loaded.url, loaded.key, loaded.org) == ("http://cp.internal", "k", "acme")


def test_the_environment_beats_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """So a container can point at a control plane without a writable home directory."""
    monkeypatch.setenv("NETI_HOME", str(tmp_path))
    save_credentials(Credentials(url="http://from-file", key="f"))
    monkeypatch.setenv("NETI_CLOUD_URL", "http://from-env")
    monkeypatch.setenv("NETI_CLOUD_KEY", "e")

    loaded = load_credentials()
    assert loaded is not None
    assert loaded.url == "http://from-env"


def test_no_credentials_is_none_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NETI_HOME", str(tmp_path / "nothing-here"))
    monkeypatch.delenv("NETI_CLOUD_URL", raising=False)
    monkeypatch.delenv("NETI_CLOUD_KEY", raising=False)
    assert load_credentials() is None


def test_the_client_parses_every_state(store: Store) -> None:
    """The wire shapes the server actually emits, through the client the gate actually uses."""
    from neti.cloud import _approval

    row = store.request("d7", {"tool": "send_email"}, 5, "recipients")
    parsed: Approval = _approval(row.as_json())
    assert parsed.state is ApprovalState.PENDING
    assert parsed.approved_magnitude == 5

    store.decide(row.id, granted=True, decided_by="sam")
    granted = _approval(store.get(row.id).as_json())  # type: ignore[union-attr]
    assert granted.state is ApprovalState.GRANTED
    assert granted.proceeds


def test_a_denial_is_not_erased_by_a_retry(api: TestClient, tenant: SyntheticTenant) -> None:
    """Found by running the fake's contract against the real store, and it was a bad one.

    `open_for` originally skipped denied rows, so a refused call found nothing on its next attempt,
    raised a fresh request, and showed the agent `pending` again — the reviewer's "no" erased, and
    the same person asked the same question on every retry.
    """
    gate = keeper(tenant, ClientApprover(api))
    gate.decide(CONFIRMING)
    api.post(
        f"/v1/approvals/{pending_id(api)}/decide", json={"granted": False, "decided_by": "alex"}
    ).raise_for_status()

    for _ in range(3):
        again = gate.decide(CONFIRMING)
        assert not again.proceeds
        assert again.escalation.state is ApprovalState.DENIED

    # And no new request was ever raised, so nobody was re-asked.
    assert len(api.get("/v1/approvals").json()["approvals"]) == 1


# ---------------------------------------------------------------------------- reaching it at all


def test_the_hook_can_be_pointed_at_a_control_plane() -> None:
    """`neti gate` had `--org` and `neti hook` did not, so the paid tier was unreachable from it.

    That is the seam for a harness's own built-in tools — the README calls it "the only seam that
    exists for those" — and it is the one most installs use. `run_hook` has always taken an
    `approver`, and `tests/e2e/test_seam_equivalence.py` proves the hook honours a granted approval,
    because the test passes one in directly. The command line never did, so nothing an operator
    could type reached a human from that seam.

    Asserted against the CLI surface rather than the function, because the function was never the
    problem.
    """
    import inspect

    from neti.cli import hook

    assert "org" in inspect.signature(hook).parameters, (
        "`neti hook` has no --org, so a CONFIRM on Claude Code's built-in tools can never reach a "
        "human however the operator is logged in"
    )


def test_a_hook_missing_its_login_degrades_instead_of_failing_the_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--org` without credentials must not take the session down.

    For `neti gate` the right answer is to refuse loudly at startup: it is one long-lived process,
    and somebody who passed `--org` believing their CONFIRMs reach a human should find out
    immediately. For `neti hook` the same exit code fails the tool call it was asked about, so every
    call in the session would fail — the failure this codebase spends most of its effort avoiding.

    So it says the same thing and carries on without an approver, which leaves a CONFIRM stopping
    the call: the free tier's behaviour, and the one the paid tier degrades to everywhere else.
    """
    from neti.cli import _approver

    monkeypatch.setenv("NETI_HOME", str(tmp_path))

    assert _approver(True, fatal=False) is None

    # `typer.Exit`, not `SystemExit` — typer raises its own and click turns it into an exit code at
    # the top of the CLI. Worth naming: catching `SystemExit` here passes for the wrong reason on a
    # future version that changes it.
    import typer

    with pytest.raises(typer.Exit) as refused:
        _approver(True, fatal=True)
    assert refused.value.exit_code == 2


def test_asking_for_no_control_plane_reaches_for_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default. Without `--org` the gate never looks for credentials at all."""
    from neti.cli import _approver

    monkeypatch.setenv("NETI_HOME", str(tmp_path))
    assert _approver(False) is None
    assert _approver(False, fatal=False) is None
