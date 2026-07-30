"""The observer must be able to watch the engine without being able to change it.

The console needs to show what `gate` did — which means reaching into the hot path, which is exactly
where a well-meaning hook becomes a security defect. These tests fix the boundary: an observer sees
everything and influences nothing, and an engine with no observer runs the code it ran before the
hook existed.
"""

from __future__ import annotations

from typing import Any

import pytest

from neti.config.policy import load_policy
from neti.core.record import verify_chain
from neti.core.types import ProposedCall
from neti.core.verdict import Mode, Verdict
from neti.engine import COMPARED, INTERCEPTED, RESOLVED, SEALED, Engine
from neti.eval.synthetic import SyntheticTenant, default_tenant
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from tests.integration.test_inventory import EXAMPLE

CRED = ClientCredential(tenant_id="t", client_id="c", client_secret="s")


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


def engine_for(tenant: SyntheticTenant) -> tuple[Engine, GraphClient]:
    policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.ENFORCE})
    client = GraphClient(CRED, transport=tenant.transport())
    return Engine(policy=policy, resolvers=resolvers_for_client(client)), client


def test_an_observer_cannot_change_the_verdict_or_the_record(tenant: SyntheticTenant) -> None:
    """The safety property. Two identical calls, one watched and one not, must agree on everything
    that matters — including the sealed digest, which covers the whole decision."""
    call = ProposedCall(tool="remove_group_members", args={"group": "g-eng-all"}, session_id="s")

    engine, client = engine_for(tenant)
    try:
        silent = engine.gate(call)
    finally:
        client.close()

    engine, client = engine_for(tenant)
    try:
        watched = engine.gate(call, observe=lambda stage, payload: None)
    finally:
        client.close()

    assert silent.decision.verdict is watched.decision.verdict
    assert silent.decision.rule == watched.decision.rule

    def judgement(result: object) -> list[tuple[str, str, str, int | None]]:
        """Per-argument verdicts, without the wall-clock a `Resolution` also carries."""
        return [
            (a.pointer, a.verdict.name, a.rule, a.resolution.magnitude)
            for a in result.decision.args  # type: ignore[attr-defined]
        ]

    assert judgement(silent) == judgement(watched)

    # Everything the decision rests on must match. `decided_at`, `decision_id` and each cause's
    # `resolved_at` differ between any two runs by design — they are wall-clock and identity, not
    # judgement — so they are excluded explicitly rather than being allowed to mask a real
    # difference by comparing something looser.
    volatile = {"decided_at", "decision_id"}
    a = {k: v for k, v in silent.record.chained.items() if k not in volatile}
    b = {k: v for k, v in watched.record.chained.items() if k not in volatile}
    for side in (a, b):
        side["causes"] = [
            {k: v for k, v in cause.items() if k != "resolved_at"} for cause in side["causes"]
        ]
    assert a == b


def test_an_observer_that_explodes_does_not_change_the_verdict(tenant: SyntheticTenant) -> None:
    """A broken console must not become a broken gate.

    Today the exception propagates, which is the honest default — it is a programming error in the
    caller, not a runtime condition, and swallowing it would hide a console bug forever. What must
    never happen is a *different verdict*: the engine has no catch that could fail open.
    """
    engine, client = engine_for(tenant)
    try:
        with pytest.raises(RuntimeError, match="console is on fire"):
            engine.gate(
                ProposedCall(tool="send_email", args={"to": "g-eng-all"}),
                observe=_explode,
            )
        # and the engine is still usable afterwards
        result = engine.gate(ProposedCall(tool="send_email", args={"to": "g-solo"}))
    finally:
        client.close()
    assert result.decision.verdict is Verdict.ALLOW


def _explode(stage: str, payload: dict[str, Any]) -> None:
    raise RuntimeError("console is on fire")


def test_the_stages_tell_the_whole_story(tenant: SyntheticTenant) -> None:
    """What the console renders. If this drifts, the theatre silently loses a beat."""
    seen: list[tuple[str, dict[str, Any]]] = []
    engine, client = engine_for(tenant)
    try:
        engine.gate(
            ProposedCall(tool="remove_group_members", args={"group": "g-eng-all"}, session_id="s"),
            observe=lambda s, p: seen.append((s, p)),
        )
    finally:
        client.close()

    order = [s for s, _ in seen]
    assert order[0] == INTERCEPTED
    assert order[-1] == SEALED
    assert order.count(RESOLVED) == 2, "two gated parameters, two resolutions"
    assert order.count(COMPARED) == 1

    by_stage = {s: p for s, p in seen}
    resolved = [p for s, p in seen if s == RESOLVED]
    assert {r["magnitude"] for r in resolved} == {41_203, 37}

    # The wire detail is what makes the screen credible rather than decorative.
    principals = next(r for r in resolved if r["unit"] == "principals")
    assert "transitiveMembers/$count" in principals["evidence"]["url"]
    assert principals["evidence"]["status"] == 200
    assert principals["direction"] == "exact"

    compared = by_stage[COMPARED]
    assert compared["verdict"] == "block"
    assert {b["above"] for a in compared["args"] for b in a["breaches"]} == {200, 5}

    sealed = by_stage[SEALED]
    assert len(sealed["record_digest"]) == 64


def test_an_unresolvable_target_still_produces_a_full_trace(tenant: SyntheticTenant) -> None:
    """The console must be able to show ignorance as clearly as it shows a number."""
    seen: list[tuple[str, dict[str, Any]]] = []
    engine, client = engine_for(tenant)
    try:
        engine.gate(
            ProposedCall(tool="send_email", args={"to": "g-ddg"}),
            observe=lambda s, p: seen.append((s, p)),
        )
    finally:
        client.close()

    resolved = next(p for s, p in seen if s == RESOLVED)
    assert resolved["state"] == "unresolved"
    assert resolved["magnitude"] is None
    assert "dynamic distribution" in resolved["evidence"]["hint"]
    assert next(p for s, p in seen if s == SEALED)["record_digest"]


def test_watching_does_not_disturb_the_chain(tenant: SyntheticTenant) -> None:
    engine, client = engine_for(tenant)
    try:
        records = [
            engine.gate(
                ProposedCall(tool="send_email", args={"to": g}, session_id="s"),
                observe=(lambda s, p: None) if i % 2 else None,
            ).record
            for i, g in enumerate(("g-solo", "g-team", "g-solo", "g-team"))
        ]
    finally:
        client.close()
    ok, bad = verify_chain(records)
    assert ok and bad is None
