"""The console API.

The property that matters here is that the API adds no judgement of its own. Every endpoint is a
thin wrapper over something already tested, and these tests exist mostly to prove the wrapper does
not quietly change the answer — because the console's entire claim is that what it shows is what the
gate does.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import pytest

warnings.filterwarnings("ignore", message=".*starlette.testclient.*")

from fastapi.testclient import TestClient  # noqa: E402

from neti.api.app import create_app  # noqa: E402
from neti.api.state import build_state  # noqa: E402


@pytest.fixture
def client(tmp_path: Path) -> Any:
    state = build_state(
        config="examples/entra.yaml", records=tmp_path / "console.ndjson", demo=True
    )
    with TestClient(create_app(state)) as c:
        yield c
    state.close()


@pytest.fixture
def connected(client: Any) -> Any:
    client.post("/api/connect")
    client.post("/api/mode", json={"mode": "enforce"})
    return client


def fire(c: Any, tool: str, target: str, session: str = "t") -> dict[str, Any]:
    arg = "group" if "group" in tool else "to"
    r = c.post("/api/gate", json={"tool": tool, "args": {arg: target}, "session_id": session})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# --------------------------------------------------------------- state & connect


def test_the_console_starts_with_no_credentials(client: Any) -> None:
    """A demo that needs a tenant before it will start is not a demo."""
    s = client.get("/api/state").json()
    assert s["mode"] == "demo"
    assert s["connected"] is False
    assert s["policy_mode"] == "observe", "the shipped example must not default to enforce"


def test_the_fixture_ground_truth_is_published(client: Any) -> None:
    """The viewer can check every resolved number against what the fixture declares, rather than
    taking the resolver's word for it."""
    fixture = client.get("/api/state").json()["fixture"]
    by_id = {g["id"]: g for g in fixture}
    assert by_id["g-eng-all"]["members"] == 41_203
    assert by_id["g-ddg"]["kind"] == "dynamic_distribution"


def test_nothing_resolves_before_connecting(client: Any) -> None:
    r = client.post("/api/gate", json={"tool": "send_email", "args": {"to": "g-solo"}})
    assert r.status_code == 409


def test_connect_proves_the_credential_by_using_it(client: Any) -> None:
    """A connect button that only stores a secret has proved nothing."""
    body = client.post("/api/connect").json()
    assert body["connected"] is True
    assert body["directory_size"] == 52_400


# --------------------------------------------------------------- the gate


def test_the_gate_returns_verdict_trace_and_record(connected: Any) -> None:
    body = fire(connected, "remove_group_members", "g-eng-all")
    assert body["verdict"] == "block"
    assert body["proceeds"] is False
    assert body["denial"]["resolved"] == 41_203
    assert body["denial"]["ceiling"] == 200

    stages = body["trace"]["stages"]
    keys = [s["key"] for s in stages]
    assert keys[0] == "intercept"
    assert keys[-1] == "seal"
    counts = [s for s in stages if s["key"] == "count"]
    assert len(counts) == 2, "two gated parameters, two counts"
    # The wire detail is the credibility — assert it is actually on the stage line.
    assert "transitiveMembers/$count" in counts[0]["detail"]
    assert "41,203 principals" in counts[0]["detail"]
    assert "→ BLOCK" in next(s for s in stages if s["key"] == "compare")["detail"]


def test_stage_timings_are_real_and_monotonic(connected: Any) -> None:
    """Displayed timings must be measured, never invented. Any pacing is a UI concern."""
    stages = fire(connected, "remove_group_members", "g-eng-all")["trace"]["stages"]
    at = [s["at_ms"] for s in stages]
    assert at == sorted(at)
    assert at[0] >= 0
    assert all(s["took_ms"] >= 0 for s in stages)


def test_observe_and_enforce_reach_the_same_verdict(connected: Any) -> None:
    """The most honest thing the console can show: enforcement changes whether the decision is
    acted on, not what the decision is."""
    connected.post("/api/mode", json={"mode": "observe"})
    observed = fire(connected, "remove_group_members", "g-eng-all")
    connected.post("/api/mode", json={"mode": "enforce"})
    enforced = fire(connected, "remove_group_members", "g-eng-all")

    assert observed["verdict"] == enforced["verdict"] == "block"
    assert observed["proceeds"] is True, "observe forwards the call — that is what makes it safe"
    assert enforced["proceeds"] is False


def test_an_unsizeable_target_is_not_read_as_zero(connected: Any) -> None:
    body = fire(connected, "send_email", "g-ddg")
    assert body["verdict"] == "confirm"
    assert body["denial"]["resolved"] is None
    count = next(s for s in body["trace"]["stages"] if s["key"] == "count")
    assert "404" in count["detail"]


def test_an_ungated_tool_passes_without_ceremony(connected: Any) -> None:
    body = fire(connected, "read_group", "g-eng-all")
    assert body["verdict"] == "allow"
    assert body["rule"] == "tool_not_gated"
    assert [s["key"] for s in body["trace"]["stages"]] == ["intercept", "compare", "seal"]


@pytest.mark.parametrize("bad", ["", "ENFORCED", "on"])
def test_an_unknown_mode_is_rejected(connected: Any, bad: str) -> None:
    assert connected.post("/api/mode", json={"mode": bad}).status_code == 400


# --------------------------------------------------------------- scenario


def test_the_offboard_scenario_ends_in_a_block(connected: Any) -> None:
    """The demo's spine. If it stops demonstrating anything, it fails here, not on stage."""
    scenario = connected.get("/api/scenarios/offboard").json()
    verdicts = []
    for step in scenario["steps"]:
        r = connected.post(
            "/api/gate",
            json={
                "tool": step["tool"],
                "args": step["args"],
                "session_id": scenario["session_id"],
            },
        )
        verdicts.append(r.json()["verdict"])

    assert verdicts == ["allow", "allow", "block"]
    assert "41,203" in scenario["moral"]


def test_every_scenario_step_reaches_its_declared_verdict(connected: Any) -> None:
    """`Step.expect` is a fixture, not decoration: a scenario that quietly stops proving its point
    should break the build."""
    from neti.eval.scenarios import SCENARIOS

    for scenario in SCENARIOS.values():
        connected.post("/api/mode", json={"mode": "enforce"})
        for step in scenario.steps:
            body = connected.post(
                "/api/gate",
                json={
                    "tool": step.tool,
                    "args": step.args,
                    "session_id": f"{scenario.session_id}-check",
                },
            ).json()
            assert body["verdict"] == step.expect, (
                f"{scenario.id}/{step.tool} reached {body['verdict']}, expected {step.expect}"
            )


# --------------------------------------------------------------- reads


def test_decisions_and_detail_round_trip(connected: Any) -> None:
    fired = fire(connected, "remove_group_members", "g-eng-all")
    listing = connected.get("/api/decisions").json()
    assert listing["total"] == 1
    assert listing["decisions"][0]["decision_id"] == fired["decision_id"]

    detail = connected.get(f"/api/decisions/{fired['decision_id']}").json()
    assert detail["verdict"] == "block"
    assert {c["pointer"] for c in detail["causes"]} == {"/group", "/group#apps"}
    assert connected.get("/api/decisions/nope").status_code == 404


def test_the_audit_chain_verifies_across_many_calls(connected: Any) -> None:
    for group in ("g-solo", "g-team", "g-dept", "g-eng-all"):
        fire(connected, "send_email", group)
    audit = connected.get("/api/audit/verify").json()
    assert audit["ok"] is True
    assert audit["broken_at"] is None
    assert audit["count"] == 4
    assert audit["links"][0]["prev_digest"] is None
    assert audit["links"][1]["prev_digest"] == audit["links"][0]["record_digest"]


def test_the_chain_survives_a_mode_change_mid_session(connected: Any) -> None:
    """Swapping mode replaces the policy, which changes the policy digest — correctly, since a
    record must say which policy produced it. The chain must not care."""
    fire(connected, "send_email", "g-solo")
    connected.post("/api/mode", json={"mode": "observe"})
    fire(connected, "send_email", "g-team")
    connected.post("/api/mode", json={"mode": "enforce"})
    fire(connected, "send_email", "g-dept")

    audit = connected.get("/api/audit/verify").json()
    assert audit["ok"] is True
    digests = {link["decision_id"] for link in audit["links"]}
    assert len(digests) == 3


def test_inventory_needs_no_traffic(connected: Any) -> None:
    rows = connected.get("/api/inventory").json()["rows"]
    assert rows
    assert max(r["reachable"] or 0 for r in rows) == 52_400
    assert all(r["direction"] == "upper_bound" for r in rows if r["reachable"]), (
        "reachable maxima are capability bounds; the procedure must never allow on one"
    )


def test_report_and_proposal_come_from_recorded_traffic(connected: Any) -> None:
    for group in ("g-solo", "g-team", "g-solo", "g-dept"):
        fire(connected, "send_email", group)
    report = connected.get("/api/report").json()
    dist = report["distributions"][0]
    assert dist["n"] == 4
    assert dist["max"] == 500
    assert report["proposals"][0]["actionable"] is False, "four calls is below the sample floor"


def test_policy_exposes_the_declared_ceilings(connected: Any) -> None:
    policy = connected.get("/api/policy").json()
    gate = policy["tools"]["remove_group_members"]["/group"]
    assert gate["resolver"] == "entra.principals"
    assert [b["above"] for b in gate["bands"]] == [200, 25]
    assert policy["session_budgets"], "NC-01 mitigation must be visible in the console"


def test_the_scorecard_still_reports_its_misses(connected: Any) -> None:
    card = connected.get("/api/scorecard").json()
    assert card["coverage"]["caught"] < card["coverage"]["total"]
    assert card["known_blind_spots"]
    assert card["not_yet_measured"]


def test_the_decision_list_says_which_rows_are_synthetic(connected: Any) -> None:
    """The console reads this endpoint, and it hand-picks its fields.

    So a field added to the record does not appear here unless somebody adds it, and this is the one
    that must not be missed: the console's whole job is showing an operator what their agents did,
    and a `--demo` row rendering beside a measured one — same confident magnitude, nothing to tell
    them apart — is exactly the defect `neti.decision.v2` exists to close, one layer up.
    """
    fire(connected, "remove_group_members", "g-eng-all")

    rows = connected.get("/api/decisions").json()["decisions"]
    assert rows, "the gate call recorded nothing"
    assert all("synthetic" in row for row in rows), (
        "the decision list dropped the synthetic marker; the console cannot distinguish invented "
        "magnitudes from measured ones"
    )
    # And the value has to be right, not merely present. The console defaults to the synthetic
    # tenant whenever there is no credential — which is most of the time anybody is looking at it —
    # so `False` here would be the more dangerous of the two wrong answers.
    assert all(row["synthetic"] is True for row in rows), (
        "the console is running on the synthetic tenant and recorded its magnitudes as measured"
    )


def test_the_console_configures_its_resolvers_from_the_policy(tmp_path: Path) -> None:
    """The console read `providers:` from nowhere, so every filesystem policy looked empty.

    `resolvers_for_client` takes the policy's `providers:` block and uses it to bound what a
    resolver can reach — without a declared root, `fs.paths` has no bound to report and declines.
    Every caller passed it except this one, and the consequence was not subtle: the console's
    headline number, *reachable in one call*, read **0** for a coding-agent policy, and the whole
    "what each tool can reach" table read `—`. The most common policy there is, and the surface
    built specifically for showing people numbers, showing none.

    Asserted through `build_state` rather than by reading the call, because what matters is that a
    resolver ends up bounded, not that an argument was passed.
    """
    from neti.api.state import build_state

    tree = tmp_path / "tree"
    tree.mkdir()
    for index in range(12):
        (tree / f"f{index}.txt").write_text("x\n", encoding="utf-8")

    config = tmp_path / "neti.yaml"
    config.write_text(
        "version: 1\n"
        "mode: observe\n"
        "providers:\n"
        "  fs:\n"
        f"    root: {tree}\n"
        "tools:\n"
        "  Glob:\n"
        "    gate:\n"
        "      /pattern:\n"
        "        resolver: fs.paths\n"
        "        bands: []\n",
        encoding="utf-8",
    )

    state = build_state(config=config, records=tmp_path / "out.ndjson", demo=True)
    from neti.resolvers.base import ResolveContext

    reachable = state.engine.resolvers["fs.paths"].reachable_max(ResolveContext())

    assert reachable is not None, (
        "fs.paths has no declared root, so the console cannot say what a call could reach — "
        "which is the number the overview leads with"
    )
    assert reachable.magnitude == 12, reachable


def test_a_filesystem_policy_is_not_called_a_demo(tmp_path: Path) -> None:
    """A coding-agent install has no directory to ask, so nothing about it is a fixture.

    The console branded every session without Entra credentials a "Demo tenant", which was wrong
    twice: a local install is the whole gate, and a policy gated on `fs.paths` produces magnitudes
    measured off real files. Worse, that same flag stamped `synthetic=True` onto those records —
    marking a real measurement as invented, which is the exact lie the flag exists to prevent,
    pointing the other way.
    """
    from neti.api.state import build_state

    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("x\n", encoding="utf-8")

    config = tmp_path / "neti.yaml"
    config.write_text(
        "version: 1\nmode: observe\nproviders:\n  fs:\n"
        f"    root: {tree}\n"
        "tools:\n  Glob:\n    gate:\n      /pattern:\n"
        "        resolver: fs.paths\n        bands: []\n",
        encoding="utf-8",
    )

    state = build_state(config=config, records=tmp_path / "out.ndjson", demo=True)

    assert not state.demo, "a filesystem-only policy has no fixture in it and is not a demo"
    assert state.engine.synthetic is False, (
        "records would be stamped synthetic although every magnitude was measured from real files"
    )
    assert "machine" in state.tenant_label, state.tenant_label


def test_an_entra_policy_with_no_credentials_still_says_so(tmp_path: Path) -> None:
    """The other direction, and the reason the flag exists at all.

    Renaming the demo away must not quietly turn fixture group sizes into findings about a real
    directory. When the policy does bind an Entra resolver and there is no credential, the numbers
    genuinely come from the fixture and both the label and the record must say it.
    """
    from neti.api.state import build_state

    config = tmp_path / "neti.yaml"
    config.write_text(
        "version: 1\nmode: observe\ntools:\n  remove_group_members:\n    gate:\n      /group:\n"
        "        resolver: entra.principals\n        bands: []\n",
        encoding="utf-8",
    )

    state = build_state(config=config, records=tmp_path / "out.ndjson", demo=True)

    assert state.demo, "an Entra policy on the fixture must still be marked"
    assert state.engine.synthetic is True
    assert "sample" in state.tenant_label.lower(), state.tenant_label
