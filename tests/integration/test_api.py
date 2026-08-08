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
    # Every axis that can stop a call has to be readable here, and this one is written by
    # `neti start` without being asked. A rule an operator cannot find in their own console is one
    # they can neither check nor remove — the failure this repository has now made eight times.
    assert "outside_root" in policy, "the location axis is invisible to the console"
    # The value, not just the key. `str(Verdict.CONFIRM)` is `"2"`, which serialises fine, renders
    # as a chip reading `2`, and passes any test that only checks the field is present.
    assert policy["outside_root"]["verdict"] in {None, "flag", "confirm", "block"}


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


def test_scenarios_are_only_offered_when_the_policy_gates_them(tmp_path: Path) -> None:
    """A console must not tell somebody a story about a tool they do not have.

    `/api/scenarios` returned the shipped Entra scenarios unconditionally, so a filesystem install
    gating `Glob`, `Read` and `delete_files` was offered "Offboard the Q3 contractors" — which
    drives `remove_group_members` against a group that does not exist. Mock data wearing the clothes
    of real data, inside the operator's own console.

    Also a regression test for calling it at all: the first version of the filter used `state()` on
    an object that is not callable, so the endpoint 500'd on every request and the change shipped
    because nothing here had ever asked it a question.
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
    client = TestClient(create_app(state))

    response = client.get("/api/scenarios")
    assert response.status_code == 200, response.text
    names = [s["id"] for s in response.json()["scenarios"]]
    assert names == [], f"a filesystem policy was offered scenarios it cannot run: {names}"


# --------------------------------------------------------------- the walkthrough


def test_the_walkthrough_endpoint_answers_on_a_first_run(tmp_path: Path) -> None:
    """`/api/start` is what the console opens on before anything has happened.

    Written because `/api/scenarios` shipped returning a 500 for exactly this reason: no test ever
    called it, the page that used it was the one nobody had traffic for, and the failure only
    showed up in a browser. An endpoint whose whole audience is people who have done nothing yet is
    the one most likely to be exercised last.
    """
    # No `demo` argument: a filesystem policy has no directory to ask, so "whatever this machine
    # can actually do" is the honest default and is exactly what a real first run gets.
    state = build_state(config="examples/coding-agent.yaml", records=tmp_path / "none.ndjson")
    try:
        with TestClient(create_app(state)) as c:
            r = c.get("/api/start")
            assert r.status_code == 200, r.text
            body = r.json()

            assert body["decisions"] == 0
            assert body["complete"] is False
            assert [s["id"] for s in body["steps"]] == [
                "policy",
                "reach",
                "install",
                "traffic",
                "ceilings",
            ]
            # The command has to name the policy this console is holding, not a placeholder.
            install = next(s for s in body["steps"] if s["id"] == "install")
            assert "examples/coding-agent.yaml" in install["command"]
    finally:
        state.close()


def test_the_walkthrough_sees_traffic_the_moment_it_is_recorded(connected: Any) -> None:
    """It is polled, and the point of polling is that a step completes while somebody is watching.

    A cached record count would make the whole thing a static page with a spinner.
    """
    assert connected.get("/api/start").json()["decisions"] == 0

    fire(connected, "remove_group_members", "g-eng-all")

    body = connected.get("/api/start").json()
    traffic = next(s for s in body["steps"] if s["id"] == "traffic")
    assert body["decisions"] == 1
    assert traffic["done"] is True


# --------------------------------------------------------------- models & policy editing


def test_the_models_endpoint_never_returns_a_key(monkeypatch: Any, client: Any) -> None:
    """The claim the whole page rests on, asserted rather than promised.

    A console that reported a key's *value* would put it in a browser, in a screenshot, and in
    whatever the browser caches — for a feature whose entire pitch is that nothing extra holds your
    secrets. `ready` is a boolean about presence and that is the only thing that crosses.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-do-not-leak-me")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-do-not-leak-me")

    r = client.get("/api/models")
    assert r.status_code == 200, r.text
    assert "do-not-leak-me" not in r.text

    providers = {p["id"]: p for p in r.json()["providers"]}
    assert providers["anthropic"]["ready"] is True
    assert providers["openai"]["ready"] is True
    # And a local runner needs no key at all, so it is always available.
    assert providers["local"]["ready"] is True
    assert len(providers["local"]["runners"]) >= 4


def test_a_provider_with_no_key_says_so_rather_than_looking_ready(
    monkeypatch: Any, client: Any
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    providers = {p["id"]: p for p in client.get("/api/models").json()["providers"]}

    assert providers["anthropic"]["ready"] is False
    assert providers["anthropic"]["env"] == "ANTHROPIC_API_KEY"


def test_the_probe_sends_no_authorization_header(monkeypatch: Any, client: Any) -> None:
    """The first version of this attached `OPENAI_API_KEY`, which would have sent the operator's key
    to whatever address they typed into a browser field — typos and hostile hosts included.

    Nothing is lost by removing it: a gateway that requires auth answers 401, and *reachable, needs
    auth* is exactly as useful an answer for a connectivity check.
    """
    import urllib.request

    seen: dict[str, Any] = {}

    def fake_open(request: Any, timeout: float = 0) -> Any:
        seen["headers"] = dict(request.headers)
        raise urllib.error.URLError("nothing is listening")

    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-travel")
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)

    r = client.post("/api/models/probe", json={"base_url": "https://someone-elses-host.example/v1"})
    assert r.status_code == 200, r.text
    assert r.json()["reachable"] is False

    lowered = {k.lower(): v for k, v in seen.get("headers", {}).items()}
    assert "authorization" not in lowered, "the probe must never carry a credential"
    assert "must-not-travel" not in r.text


def test_the_probe_says_why_rather_than_just_no(client: Any) -> None:
    """ "Connection refused on 11434" and "404 at /v1/models" send somebody to completely different
    places. Flattening both to "unreachable" throws away the useful half."""
    r = client.post("/api/models/probe", json={"base_url": "not-a-url"})
    assert r.json()["reachable"] is False
    assert "http" in r.json()["reason"]

    assert client.post("/api/models/probe", json={"base_url": ""}).status_code == 400


def test_declaring_a_ceiling_plans_before_it_writes(tmp_path: Path) -> None:
    """Two calls, and the first one touches nothing.

    Also the endpoint test `/api/scenarios` never had: it shipped returning a 500 because no test
    called it, and the page that used it was the one nobody had traffic for.
    """
    from neti.api.state import build_state

    config = tmp_path / "neti.yaml"
    config.write_text(
        Path("examples/coding-agent.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    original = config.read_text(encoding="utf-8")

    state = build_state(config=config, records=tmp_path / "d.ndjson")
    try:
        with TestClient(create_app(state)) as c:
            body = {
                "tool": "Glob",
                "pointer": "/pattern",
                "bands": [{"above": 500, "verdict": "block"}],
            }

            planned = c.post("/api/policy/ceiling", json={**body, "apply": False})
            assert planned.status_code == 200, planned.text
            assert planned.json()["applied"] is False
            assert "+        bands:" in planned.json()["diff"]
            assert config.read_text(encoding="utf-8") == original, "planning must write nothing"

            before_digest = c.get("/api/policy").json()["digest"]
            written = c.post("/api/policy/ceiling", json={**body, "apply": True})
            assert written.status_code == 200, written.text
            assert written.json()["applied"] is True
            assert written.json()["backup"], "the previous version has to survive"

            # The running console now describes the policy that is on disk, not the one it started
            # with — and the digest moved, which is correct: a record says which policy decided it.
            after = c.get("/api/policy").json()
            assert after["digest"] != before_digest
            assert after["tools"]["Glob"]["/pattern"]["has_ceiling"] is True
    finally:
        state.close()


def test_the_location_axis_can_be_turned_off_from_where_it_was_found(tmp_path: Path) -> None:
    """`neti start` writes this without being asked, and it can stop a call.

    A rule somebody cannot switch off from the page that shows it is one they switch off by
    uninstalling the product — the same argument that made the off-limits list editable. Asserted
    against the *file*, because the response reporting `applied: true` is exactly what a broken
    splice would also report.
    """
    from neti.api.state import build_state

    config = tmp_path / "neti.yaml"
    config.write_text(
        Path("examples/coding-agent.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    original = config.read_text(encoding="utf-8")
    comments = sum(1 for line in original.splitlines() if line.strip().startswith("#"))

    state = build_state(config=config, records=tmp_path / "d.ndjson")
    try:
        with TestClient(create_app(state)) as c:
            assert c.get("/api/policy").json()["outside_root"]["verdict"] is None

            planned = c.post("/api/policy/outside_root", json={"verdict": "confirm"})
            assert planned.json()["applied"] is False
            assert "outside_root" not in config.read_text(encoding="utf-8"), (
                "planning writes nothing"
            )

            c.post("/api/policy/outside_root", json={"verdict": "confirm", "apply": True})
            assert c.get("/api/policy").json()["outside_root"]["verdict"] == "confirm"

            # Off means the line is gone, not `outside_root: allow`. Those are different states:
            # one is an axis nobody declared, the other is a declared decision to permit.
            c.post("/api/policy/outside_root", json={"verdict": "", "apply": True})
            after = config.read_text(encoding="utf-8")
            assert "outside_root" not in after
            assert c.get("/api/policy").json()["outside_root"]["verdict"] is None
            assert sum(1 for line in after.splitlines() if line.strip().startswith("#")) == (
                comments
            ), "an edit through the console must not cost the file its comments"

            # On and off again leaves the file byte-identical. Toggling a setting is the thing an
            # operator does most while deciding whether to keep it, and a splice that drifted by a
            # blank line each time would be invisible until the file was unreadable.
            c.post("/api/policy/outside_root", json={"verdict": "confirm", "apply": True})
            c.post("/api/policy/outside_root", json={"verdict": "", "apply": True})
            assert config.read_text(encoding="utf-8") == after

            assert c.post("/api/policy/outside_root", json={"verdict": "maybe"}).status_code == 400
    finally:
        state.close()


def test_a_ceiling_that_would_not_load_is_refused_by_the_endpoint(tmp_path: Path) -> None:
    from neti.api.state import build_state

    config = tmp_path / "neti.yaml"
    config.write_text(
        Path("examples/coding-agent.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    original = config.read_text(encoding="utf-8")

    state = build_state(config=config, records=tmp_path / "d.ndjson")
    try:
        with TestClient(create_app(state)) as c:
            r = c.post(
                "/api/policy/ceiling",
                json={
                    "tool": "Glob",
                    "pointer": "/pattern",
                    "bands": [{"above": 10, "verdict": "explode"}],
                    "apply": True,
                },
            )
            assert r.status_code == 400
            assert config.read_text(encoding="utf-8") == original
    finally:
        state.close()


def test_a_filesystem_policy_reports_nothing_to_connect(tmp_path: Path) -> None:
    """`/connect` led with "Microsoft 365 · Entra ID — Connected to this machine" for a coding-agent
    install: a product name over a status that was not about it, for a credential the install
    neither has nor needs.

    The same wrong assumption that once branded a filesystem gate a "Demo tenant" and offered its
    fixture groups as targets, surviving in the one page a new arrival is sent to first. `connected`
    already said the right thing; nothing had ever asked whether there was anything to connect *to*.
    """
    from neti.api.state import build_state

    state = build_state(config="examples/coding-agent.yaml", records=tmp_path / "d.ndjson")
    try:
        with TestClient(create_app(state)) as c:
            s = c.get("/api/state").json()
            assert s["binds_directory"] is False
            assert set(s["resolvers"]) == {"fs.paths", "shell.paths"}
            # And it is connected, because there is nothing to connect: a gate measuring real files
            # off this machine is not an install that is missing something.
            assert s["connected"] is True
    finally:
        state.close()


def test_an_entra_policy_still_reports_a_directory(tmp_path: Path) -> None:
    """The other half of the same branch — the case the page was built for must keep working."""
    from neti.api.state import build_state

    state = build_state(config="examples/entra.yaml", records=tmp_path / "d.ndjson", demo=True)
    try:
        with TestClient(create_app(state)) as c:
            s = c.get("/api/state").json()
            assert s["binds_directory"] is True
            assert any(r.startswith("entra.") for r in s["resolvers"])
    finally:
        state.close()


def test_the_trace_narrates_the_resolver_that_actually_ran(tmp_path: Path) -> None:
    """The live gate's own lede is *nothing here is pre-recorded*, and three of its lines were.

    A `shell.paths` call that read the local filesystem was narrated as an authorised HTTP request
    to Microsoft — `GroupMember.Read.All`, `ConsistencyLevel: eventual`, `GET → 200` — about a
    decision that was entirely real. Not a stale label: invented provenance presented as evidence,
    in a security product, on the page built to be believed.
    """
    from neti.api.state import build_state

    state = build_state(config="examples/coding-agent.yaml", records=tmp_path / "d.ndjson")
    try:
        with TestClient(create_app(state)) as c:
            r = c.post(
                "/api/gate",
                json={"tool": "Bash", "args": {"command": "rm -rf build"}, "session_id": "t"},
            )
            assert r.status_code == 200, r.text
            details = " ".join(s["detail"] for s in r.json()["trace"]["stages"])

            assert "GroupMember" not in details, "a filesystem gate holds no Graph scope"
            assert "ConsistencyLevel" not in details, "no Graph header was sent"
            assert "GET " not in details, "no request was made"
            assert "no credential" in details
            # And the resolver has to travel with the stage, which is what made any of this
            # knowable — the console could not tell which resolver ran.
            assert any(s.get("payload", {}).get("resolver") for s in r.json()["trace"]["stages"])
    finally:
        state.close()


def test_the_entra_trace_keeps_its_wire(connected: Any) -> None:
    """The other half: the case those lines were written for must still show them."""
    result = fire(connected, "remove_group_members", "g-eng-all")
    details = " ".join(s["detail"] for s in result["trace"]["stages"])

    assert "GroupMember.Read.All" in details
    assert "ConsistencyLevel: eventual" in details
    assert "GET /groups/" in details


def test_the_console_can_see_all_three_axes(tmp_path: Path) -> None:
    """A rule you cannot see is a rule you cannot check, which is most of what `/policy` is for.

    Two axes shipped in the engine with the console knowing about neither — the page listed ceilings
    and nothing else, so an operator with a `sensitive:` block had no way to read their own policy
    back. The same shape as every Entra assumption in this file: capability the UI does not reflect.
    """
    from neti.api.state import build_state

    config = tmp_path / "neti.yaml"
    config.write_text(
        "version: 1\n"
        "sensitive:\n"
        '  - { match: "**/.env", verdict: block, why: credentials }\n'
        "provenance:\n"
        "  untrusted: [tickets/**]\n"
        "  bands: [{ above: 5, verdict: confirm }]\n"
        "tools:\n"
        "  Glob:\n"
        "    gate:\n"
        "      /pattern: { resolver: fs.paths, on_unresolved: allow }\n",
        encoding="utf-8",
    )

    state = build_state(config=config, records=tmp_path / "d.ndjson")
    try:
        with TestClient(create_app(state)) as c:
            body = c.get("/api/policy").json()

            assert body["sensitive"] == [
                {"match": "**/.env", "verdict": "block", "why": "credentials"}
            ]
            assert body["provenance"]["untrusted"] == ["tickets/**"]
            assert body["provenance"]["bands"] == [{"above": 5, "verdict": "confirm"}]
    finally:
        state.close()


def test_a_decision_row_says_why_when_the_reason_was_not_a_number(tmp_path: Path) -> None:
    """Both new axes fire precisely on calls whose magnitude is unremarkable, so the number is the
    least informative thing about them. A row reading "Blocked · 1 object" is unactionable."""
    from neti.api.state import build_state

    tree = tmp_path / "repo"
    tree.mkdir()
    (tree / ".env").write_text("KEY=x", encoding="utf-8")

    config = tmp_path / "neti.yaml"
    config.write_text(
        "version: 1\nmode: enforce\n"
        f"providers: {{ fs: {{ root: {tree} }} }}\n"
        "sensitive:\n"
        '  - { match: "**/.env", verdict: block, why: credentials live here }\n'
        "tools:\n"
        "  delete:\n"
        "    gate:\n"
        "      /path: { resolver: fs.paths, on_unresolved: block }\n",
        encoding="utf-8",
    )

    state = build_state(config=config, records=tmp_path / "d.ndjson")
    try:
        with TestClient(create_app(state)) as c:
            c.post("/api/gate", json={"tool": "delete", "args": {"path": str(tree / ".env")}})
            row = c.get("/api/decisions").json()["decisions"][0]

            assert row["verdict"] == "block"
            assert row["sensitive"][0]["match"] == "**/.env"
            assert row["sensitive"][0]["why"] == "credentials live here"
    finally:
        state.close()


def test_off_limits_rules_can_be_added_and_removed_without_the_yaml(tmp_path: Path) -> None:
    """The whole argument for the policy page: the YAML is optional.

    `neti start` now writes off-limits rules without being asked, and a rule you cannot remove from
    where you found it is a rule you disable by uninstalling the product.
    """
    from neti.api.state import build_state

    config = tmp_path / "neti.yaml"
    config.write_text(
        Path("examples/coding-agent.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    comments = sum(
        1
        for line in config.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("#")
    )

    state = build_state(config=config, records=tmp_path / "d.ndjson")
    try:
        with TestClient(create_app(state)) as c:
            assert c.get("/api/policy").json()["sensitive"] == []

            rules = [{"match": "**/.env", "verdict": "confirm", "why": "credentials"}]
            planned = c.post("/api/policy/sensitive", json={"rules": rules, "apply": False})
            assert planned.status_code == 200, planned.text
            assert planned.json()["applied"] is False
            assert c.get("/api/policy").json()["sensitive"] == [], "planning writes nothing"

            c.post("/api/policy/sensitive", json={"rules": rules, "apply": True})
            assert c.get("/api/policy").json()["sensitive"] == rules

            # And removing the last one leaves no empty `sensitive:` key behind.
            c.post("/api/policy/sensitive", json={"rules": [], "apply": True})
            assert c.get("/api/policy").json()["sensitive"] == []

            after = config.read_text(encoding="utf-8")
            assert sum(1 for x in after.splitlines() if x.strip().startswith("#")) == comments
    finally:
        state.close()
