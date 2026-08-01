"""The scorecard, and the honesty properties it has to keep.

Most of these are guards against drift in the direction of marketing. A scorecard is only worth
publishing if it cannot quietly become a coverage claim, so the tests assert on what it *admits*
as much as on what it reports.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from neti.config.policy import Policy, load_policy
from neti.core.units import Unit
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.incidents import INCIDENTS, Coverage, replay
from neti.eval.scorecard import NON_COVERAGE, build_scorecard, format_scorecard, scorecard_json
from neti.eval.synthetic import SyntheticTenant, default_tenant
from neti.gateway.mcp import McpGateway
from neti.insight.report import build_report
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from neti.store.jsonl import JsonlSink, read_records
from tests.integration.test_gateway import FakeUpstream, call
from tests.integration.test_inventory import EXAMPLE

SCOPE_MD = Path(__file__).resolve().parents[2] / "SCOPE.md"
CRED = ClientCredential(tenant_id="t", client_id="c", client_secret="s")


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


# --------------------------------------------------------------- honesty guards


def test_the_blind_spot_list_matches_scope_md() -> None:
    """Docs and code must not drift.

    SCOPE.md is the artifact a customer reads and the scorecard is the artifact they are shown;
    if the two disagree about what is not covered, the honest one is whichever is less flattering
    and nobody can tell which that is.
    """
    documented = set(re.findall(r"\*\*(NC-\d+)\*\*", SCOPE_MD.read_text(encoding="utf-8")))
    assert documented, "no NC ids found in SCOPE.md — has the format changed?"
    assert documented == set(NON_COVERAGE), (
        f"SCOPE.md and the scorecard disagree: only in docs {documented - set(NON_COVERAGE)}, "
        f"only in code {set(NON_COVERAGE) - documented}"
    )


def test_the_corpus_does_not_claim_the_pocketos_incident() -> None:
    """The nine-second deletion stays a miss. Claiming it is the overclaim that loses a security
    audience in one question.

    This guard used to pin the literal sentence "Do not claim this incident", and shipping
    `storage.objects` is exactly the moment that phrasing needed to change — the note now has to
    explain why a bytes resolver *existing* still does not close this. So the assertion moved to
    the substance: it must stay a miss, and it must name both reasons it is one. A resolver that
    counts object-store prefixes does not size a Railway block volume, and the proximate cause was
    an unscoped credential either way.
    """
    pocketos = next(i for i in INCIDENTS if i.id == "pocketos-railway")
    assert pocketos.coverage is not Coverage.CAUGHT
    assert "MISS" in pocketos.note
    assert "Railway resolver" in pocketos.note, "the note must name what is actually missing"
    assert "credential" in pocketos.note, "the authorization cause is upstream and stays stated"


def test_the_corpus_attributes_the_nine_second_deletion_correctly() -> None:
    """It was PocketOS/Railway/Cursor, not Replit, and Railway restored within the hour."""
    pocketos = next(i for i in INCIDENTS if i.id == "pocketos-railway")
    assert "Cursor" in pocketos.actor
    assert "Railway" in pocketos.what_one_call_did
    assert "disputed" in pocketos.reversible
    assert not any("replit" in i.id.lower() for i in INCIDENTS), (
        "the Replit story was the wrong attribution for this incident"
    )


def test_every_incident_carries_a_source() -> None:
    for incident in INCIDENTS:
        assert incident.source, f"{incident.id} has no source"


def test_the_scorecard_reports_more_misses_than_catches() -> None:
    """Not a target — an accurate description of the current corpus, and a tripwire.

    If this ever inverts because entries were reclassified rather than resolvers built, the change
    should be deliberate and visible in a diff.
    """
    card = build_scorecard()
    caught = len(card.incidents[Coverage.CAUGHT.value])
    assert caught < card.total_incidents - caught


def test_coverage_follows_what_actually_ships() -> None:
    """Remove the principals resolver and the flagship case stops counting as covered."""
    without = replay(frozenset({Unit.RECIPIENTS}))
    ids = {i.id for i in without[Coverage.CAUGHT.value]}
    assert "remove-group-members" not in ids
    assert "nhs-email-storm" in ids


def test_the_output_states_what_is_not_measured(tenant: SyntheticTenant) -> None:
    text = format_scorecard(build_scorecard())
    assert "NOT YET MEASURED" in text
    assert "REQUIRES A LIVE TENANT" in text
    assert "modelled" in text, "latency must not be presented as measured"
    assert "UNVERIFIED" in text, "the guest-filter risk must stay visible"


def test_blind_spots_are_in_the_body_not_an_appendix() -> None:
    """They must appear before the closing section, where they will actually be read."""
    text = format_scorecard(build_scorecard())
    assert text.index("KNOWN BLIND SPOTS") < text.index("NOT YET MEASURED")
    assert "MISS" in text.split("M5")[0], "the incident table must lead with its misses visible"


def test_a_gate_that_sizes_a_different_quantity_says_so() -> None:
    """The Terraform catch is real but the units differ, and the table must not blur them.

    1,943,200 rows were lost; what the gate sees is a handful of resources in a plan. Printing the
    frightening number next to a tick, with no note that it is not the quantity measured, is the
    kind of slide that gets taken apart in the room.
    """
    tf = next(i for i in INCIDENTS if i.id == "claude-code-terraform")
    assert tf.coverage is Coverage.CAUGHT
    assert tf.unit is Unit.ROWS, "the harm was rows"
    assert tf.gated_unit is Unit.RESOURCES, "the gate sizes resources"
    assert tf.sizing_unit is Unit.RESOURCES, "coverage is decided by what is measurable"
    assert "not the same quantity" in tf.note

    text = format_scorecard(build_scorecard())
    assert "rows (gated: resources)" in text, "the divergence must be on the row, not just in prose"


def test_dropping_the_terraform_resolver_reclassifies_its_incident() -> None:
    """Coverage tracks what ships. Removing a resolver must move its entry back to a miss."""
    without = replay(frozenset({Unit.PRINCIPALS, Unit.RECIPIENTS}))
    assert "claude-code-terraform" not in {i.id for i in without[Coverage.CAUGHT.value]}
    assert "claude-code-terraform" in {i.id for i in without[Coverage.NEEDS_RESOLVER.value]}


# --------------------------------------------------------------- friction (M5)


def test_friction_is_measured_from_real_recorded_traffic(
    tenant: SyntheticTenant, tmp_path: Any
) -> None:
    policy: Policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.ENFORCE})
    path = tmp_path / "d.ndjson"
    with JsonlSink(path) as sink:
        client = GraphClient(CRED, transport=tenant.transport())
        try:
            gate = McpGateway(
                engine=Engine(policy=policy, resolvers=resolvers_for_client(client)),
                upstream=FakeUpstream(),
                sink=sink,
            )
            for group in ("g-solo", "g-team", "g-dept", "g-eng-all", "g-ddg"):
                gate.handle(call("send_email", {"to": group}), "s")
        finally:
            client.close()

    card = build_scorecard(build_report(read_records(path)), policy)
    assert card.friction.calls == 5
    assert card.friction.stopped == card.friction.blocked + card.friction.confirmed
    assert 0 < card.friction.interrupt_rate <= 1
    assert card.unresolved == 1, "the dynamic distribution group is one unsizeable parameter"

    text = format_scorecard(card)
    assert "interrupt rate" in text
    assert "could not be resolved" in text


def test_a_policy_with_no_ceilings_is_called_out(tenant: SyntheticTenant) -> None:
    """A gate that resolves but cannot block is observe-mode value, not protection."""
    policy = Policy.model_validate(
        {"tools": {"send_email": {"gate": {"/to": {"resolver": "entra.principals"}}}}}
    )
    card = build_scorecard(None, policy)
    assert card.params_without_ceiling == 1
    assert "cannot block" in format_scorecard(card)


def test_scorecard_runs_with_nothing_at_all() -> None:
    """The incident replay and the blind spots need no traffic and no policy — that is the point."""
    text = format_scorecard(build_scorecard())
    assert "no recorded traffic" in text
    assert "INCIDENT REPLAY" in text


# --------------------------------------------------------------- json


def test_json_output_is_valid_and_carries_the_blind_spots() -> None:
    payload = json.loads(scorecard_json(build_scorecard()))
    assert payload["coverage"]["caught"] < payload["coverage"]["total"]
    assert set(payload["known_blind_spots"]) == set(NON_COVERAGE)
    assert payload["not_yet_measured"], "the JSON must carry the caveats too, not just the terminal"
    # units serialise as their string value, not as a Python repr
    for entries in payload["incidents"].values():
        for entry in entries:
            assert entry["unit"] is None or isinstance(entry["unit"], str)
