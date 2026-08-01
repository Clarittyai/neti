"""The engine end to end, against the synthetic tenant.

The tests that matter here are the stateful ones. Everything else is already covered by the pure
core; the engine's own risk is session budget accounting, which has exactly one rule that is easy to
get wrong and expensive to get wrong.
"""

from __future__ import annotations

import pytest

from neti.config.policy import Policy, load_policy
from neti.core.record import verify_chain
from neti.core.types import ProposedCall
from neti.core.units import Unit
from neti.core.verdict import Mode, Verdict
from neti.engine import Engine
from neti.eval.synthetic import Group, SyntheticTenant, default_tenant
from neti.resolvers.base import ResolveContext
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from tests.integration.test_inventory import EXAMPLE

CRED = ClientCredential(tenant_id="t", client_id="c", client_secret="s")


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


def engine_for(tenant: SyntheticTenant, policy: Policy) -> tuple[Engine, GraphClient]:
    client = GraphClient(CRED, transport=tenant.transport())
    return (
        Engine(policy=policy, resolvers=resolvers_for_client(client), ctx=ResolveContext()),
        client,
    )


def enforcing(**overrides: object) -> Policy:
    policy = load_policy(EXAMPLE)
    return policy.model_copy(update={"mode": Mode.ENFORCE, **overrides})


def test_large_group_removal_is_blocked_and_never_reaches_the_tool(
    tenant: SyntheticTenant,
) -> None:
    """The demo, and the one case the whole product exists for."""
    engine, client = engine_for(tenant, enforcing())
    try:
        result = engine.gate(
            ProposedCall(tool="remove_group_members", args={"group": "g-eng-all"}, session_id="s")
        )
    finally:
        client.close()

    assert result.decision.verdict is Verdict.BLOCK
    assert not result.proceeds
    payload = engine.denial_payload(result)
    assert payload["unit"] == "principals"
    assert payload["resolved"] == 41_203
    assert payload["ceiling"] == 200


def test_small_group_removal_proceeds(tenant: SyntheticTenant) -> None:
    engine, client = engine_for(tenant, enforcing())
    try:
        result = engine.gate(
            ProposedCall(tool="remove_group_members", args={"group": "g-solo"}, session_id="s")
        )
    finally:
        client.close()
    assert result.decision.verdict is Verdict.ALLOW
    assert result.proceeds


def test_observe_mode_records_a_block_but_still_proceeds(tenant: SyntheticTenant) -> None:
    """The property that makes installing neti reversible, and the reason observe is the default."""
    engine, client = engine_for(tenant, load_policy(EXAMPLE))
    try:
        result = engine.gate(
            ProposedCall(tool="remove_group_members", args={"group": "g-eng-all"}, session_id="s")
        )
    finally:
        client.close()
    assert result.decision.verdict is Verdict.BLOCK
    assert result.proceeds
    assert result.record.mode == "observe"


def test_ungated_tool_is_allowed_not_denied(tenant: SyntheticTenant) -> None:
    """SCOPE.md NC-09. Failing closed on everything undeclared would get the gate switched off."""
    engine, client = engine_for(tenant, enforcing())
    try:
        result = engine.gate(ProposedCall(tool="read_calendar", args={"id": "x"}))
    finally:
        client.close()
    assert result.decision.verdict is Verdict.ALLOW
    assert result.decision.rule == "tool_not_gated"


def test_dynamic_distribution_group_does_not_read_as_empty(tenant: SyntheticTenant) -> None:
    engine, client = engine_for(tenant, enforcing())
    try:
        result = engine.gate(ProposedCall(tool="send_email", args={"to": "g-ddg"}))
    finally:
        client.close()
    assert result.decision.verdict is Verdict.CONFIRM  # send_email declares on_unresolved: confirm
    assert not result.proceeds
    assert engine.denial_payload(result)["resolved"] is None


def test_absent_gated_argument_is_not_a_free_pass(tenant: SyntheticTenant) -> None:
    engine, client = engine_for(tenant, enforcing())
    try:
        result = engine.gate(ProposedCall(tool="remove_group_members", args={}))
    finally:
        client.close()
    assert result.decision.verdict is Verdict.BLOCK
    assert (
        "absent" in result.record.causes[0]["state"]
        or result.record.causes[0]["state"] == "unresolved"
    )


# --------------------------------------------------------------- session budgets (NC-01)


def test_many_single_recipient_sends_eventually_trip_the_session_budget(
    tenant: SyntheticTenant,
) -> None:
    """The NC-01 hole, closed end to end rather than in a unit test.

    Each call resolves to 1 recipient and passes every per-call ceiling. Only the declared
    cumulative budget sees the pattern.
    """
    for i in range(300):
        tenant.add(Group(f"g-one-{i}", f"Solo {i}", transitive_members=1))

    engine, client = engine_for(tenant, enforcing())
    try:
        verdicts = [
            engine.gate(
                ProposedCall(tool="send_email", args={"to": f"g-one-{i}"}, session_id="burst")
            ).decision.verdict
            for i in range(300)
        ]
    finally:
        client.close()

    assert verdicts[0] is Verdict.ALLOW
    assert Verdict.CONFIRM in verdicts, "the 200-recipient session budget never fired"
    # confirm at >200 cumulative, so the first 200 pass and the rest do not
    assert verdicts.index(Verdict.CONFIRM) == 200


def test_stopped_calls_do_not_consume_budget(tenant: SyntheticTenant) -> None:
    """One rejected call must not poison the rest of the session."""
    engine, client = engine_for(tenant, enforcing())
    try:
        blocked = engine.gate(
            ProposedCall(tool="send_email", args={"to": "g-eng-all"}, session_id="s")
        )
        assert not blocked.proceeds
        assert engine.session_total("s", Unit.RECIPIENTS) == 0

        allowed = engine.gate(
            ProposedCall(tool="send_email", args={"to": "g-team"}, session_id="s")
        )
        assert allowed.proceeds
        assert engine.session_total("s", Unit.RECIPIENTS) == 25
    finally:
        client.close()


def test_sessions_are_independent(tenant: SyntheticTenant) -> None:
    engine, client = engine_for(tenant, enforcing())
    try:
        engine.gate(ProposedCall(tool="send_email", args={"to": "g-team"}, session_id="a"))
        assert engine.session_total("a", Unit.RECIPIENTS) == 25
        assert engine.session_total("b", Unit.RECIPIENTS) == 0
    finally:
        client.close()


def test_a_budget_that_can_never_fire_is_rejected_at_construction(
    tenant: SyntheticTenant,
) -> None:
    """Dead config must be loud. This exact mismatch shipped silently once.

    A `recipients` budget over a tool whose only gated parameter produces `principals` never
    aggregates anything, so the NC-01 mitigation is absent while looking present — the worst
    possible failure mode for a security control.
    """
    policy = Policy.model_validate(
        {
            "tools": {"send_email": {"gate": {"/to": {"resolver": "entra.principals"}}}},
            "session_budgets": [
                {
                    "tools": ["send_email"],
                    "unit": "recipients",
                    "bands": [{"above": 200, "verdict": "confirm"}],
                }
            ],
        }
    )
    client = GraphClient(CRED, transport=tenant.transport())
    try:
        with pytest.raises(ValueError, match="can never fire"):
            Engine(policy=policy, resolvers=resolvers_for_client(client))
    finally:
        client.close()


def test_declaring_the_unit_on_the_gate_resolves_the_mismatch(tenant: SyntheticTenant) -> None:
    policy = Policy.model_validate(
        {
            "tools": {
                "send_email": {
                    "gate": {"/to": {"resolver": "entra.principals", "unit": "recipients"}}
                }
            },
            "session_budgets": [
                {
                    "tools": ["send_email"],
                    "unit": "recipients",
                    "bands": [{"above": 200, "verdict": "confirm"}],
                }
            ],
        }
    )
    client = GraphClient(CRED, transport=tenant.transport())
    try:
        engine = Engine(policy=policy, resolvers=resolvers_for_client(client))
        result = engine.gate(ProposedCall(tool="send_email", args={"to": "g-team"}, session_id="s"))
    finally:
        client.close()
    assert engine.session_total("s", Unit.RECIPIENTS) == 25
    # the relabel is visible in the record rather than silently overwriting what was measured
    assert result.record.causes[0]["unit"] == "recipients"


def test_the_shipped_example_passes_the_budget_unit_check(tenant: SyntheticTenant) -> None:
    client = GraphClient(CRED, transport=tenant.transport())
    try:
        Engine(policy=load_policy(EXAMPLE), resolvers=resolvers_for_client(client))
    finally:
        client.close()


# --------------------------------------------------------------- records


def test_records_form_a_verifiable_chain(tenant: SyntheticTenant) -> None:
    engine, client = engine_for(tenant, enforcing())
    try:
        records = [
            engine.gate(ProposedCall(tool="send_email", args={"to": g}, session_id="s")).record
            for g in ("g-solo", "g-team", "g-dept")
        ]
    finally:
        client.close()

    ok, bad = verify_chain(records)
    assert ok and bad is None
    assert all(r.policy_digest == records[0].policy_digest for r in records)
    assert records[0].prev_digest is None
    assert records[1].prev_digest == records[0].record_digest


def test_record_carries_the_evidence_for_the_verdict(tenant: SyntheticTenant) -> None:
    """A verdict a third party cannot check is not evidence."""
    engine, client = engine_for(tenant, enforcing())
    try:
        result = engine.gate(
            ProposedCall(tool="remove_group_members", args={"group": "g-eng-all"}, session_id="s")
        )
    finally:
        client.close()

    cause = next(c for c in result.record.causes if c["pointer"] == "/group")
    assert cause["magnitude"] == 41_203
    assert cause["unit"] == "principals"
    assert cause["direction"] == "exact"
    assert cause["consistency"] == "eventual"
    assert cause["ceiling"] == 200
    assert cause["resolved_at"] is not None
    assert {b["source"] for b in cause["breaches"]} == {"magnitude"}


def test_a_misspelled_resolver_is_refused_at_construction(tenant: SyntheticTenant) -> None:
    """Silent dead config, the sibling of the session-budget-unit check.

    `resolver: entra.principal` — one letter short of `principals` — is caught today only when a
    call arrives, and then only because `on_unresolved` happens to be `block`. Set it to `allow`
    and the parameter is simply never gated, with nothing anywhere saying so: the operator believes
    they declared a ceiling and no call is ever measured against it.

    Refused at construction, because the person who made the typo is at the keyboard now.
    """
    policy = load_policy(EXAMPLE)
    broken = policy.model_copy(
        update={
            "tools": {
                **policy.tools,
                "remove_group_members": policy.tools["remove_group_members"].model_copy(
                    update={
                        "gate": {
                            "/group": policy.tools["remove_group_members"]
                            .gate["/group"]
                            .model_copy(update={"resolver": "entra.principal"})
                        }
                    }
                ),
            }
        }
    )
    client = GraphClient(CRED, transport=tenant.transport())

    with pytest.raises(ValueError) as caught:
        Engine(policy=broken, resolvers=resolvers_for_client(client))

    message = str(caught.value)
    assert "entra.principal'" in message
    # The mistake is nearly always a near-miss, so the message has to show the real names.
    assert "entra.principals" in message


def test_a_correct_policy_still_constructs(tenant: SyntheticTenant) -> None:
    """The guard must not reject the policy we ship as an example."""
    client = GraphClient(CRED, transport=tenant.transport())
    assert Engine(policy=load_policy(EXAMPLE), resolvers=resolvers_for_client(client))


def test_the_guest_breakdown_actually_fires(tenant: SyntheticTenant) -> None:
    """The bug that was live in the shipped example for the whole life of the file.

    `examples/entra.yaml` banded `guest: above 100 → block` on `send_email/to` while the bound
    resolver emitted no breakdown at all. The fixture group has 412 guests. The rule never fired
    once, and nothing anywhere said so — the external share is a claimed differentiator that did
    nothing.

    Asserted against the shipped example on purpose: a test using its own hand-built policy would
    have passed throughout the period the real one was broken.
    """
    client = GraphClient(CRED, transport=tenant.transport())
    engine = Engine(
        policy=load_policy(EXAMPLE).model_copy(update={"mode": Mode.ENFORCE}),
        resolvers=resolvers_for_client(client),
    )
    result = engine.gate(ProposedCall(tool="send_email", args={"to": "g-eng-all"}))
    arg = result.decision.args[0]

    assert dict(arg.resolution.breakdown) == {"guest": 412, "internal": 40_791}
    assert ("breakdown:guest", 412, 100) in [(b.source, b.observed, b.above) for b in arg.breaches]
    assert result.decision.verdict is Verdict.BLOCK


def test_a_breakdown_band_nothing_emits_is_refused(tenant: SyntheticTenant) -> None:
    """The guard for the class, not the instance.

    `decide` skipping an absent breakdown key is correct and has to stay — a resolver whose guest
    lookup failed must not be read as reporting zero guests, which would turn a failed lookup into
    a permissive verdict. That correctness is exactly what made the typo invisible, so the check
    belongs at construction, where both the policy and the resolvers are known.
    """
    policy = load_policy(EXAMPLE)
    gate = policy.tools["send_email"].gate["/to"]
    # Point the banded gate back at the single-count resolver, which emits no breakdown.
    broken = policy.model_copy(
        update={
            "tools": {
                **policy.tools,
                "send_email": policy.tools["send_email"].model_copy(
                    update={
                        "gate": {"/to": gate.model_copy(update={"resolver": "entra.principals"})}
                    }
                ),
            }
        }
    )
    client = GraphClient(CRED, transport=tenant.transport())

    with pytest.raises(ValueError) as caught:
        Engine(policy=broken, resolvers=resolvers_for_client(client))

    message = str(caught.value)
    assert "would never fire" in message
    assert "'guest'" in message
    assert "emits no breakdown at all" in message


def test_every_resolver_declares_what_it_emits(tenant: SyntheticTenant) -> None:
    """Without this the guard has nothing to check against and silently permits everything."""
    client = GraphClient(CRED, transport=tenant.transport())
    resolvers = resolvers_for_client(client)

    for name, resolver in resolvers.items():
        assert isinstance(resolver.breakdown_keys, frozenset), name

    assert resolvers["entra.principals"].breakdown_keys == frozenset()
    assert resolvers["entra.principals_with_guests"].breakdown_keys == {"guest", "internal"}
    assert "destroy" in resolvers["terraform.destroy"].breakdown_keys


def test_the_cheap_resolver_stays_one_request(tenant: SyntheticTenant) -> None:
    """The latency claim the whole design rests on.

    The breakdown costs a second Graph round trip, so it lives under its own name. If it were
    folded into `entra.principals`, every gated call in every policy would quietly pay double.
    """
    client = GraphClient(CRED, transport=tenant.transport())
    resolvers = resolvers_for_client(client)
    # Warm up first: the very first call also fetches a token, and counting that would measure
    # authentication rather than the thing under test.
    resolvers["entra.principals"].resolve("g-team", ResolveContext())
    before = len(tenant.headers_seen)

    resolvers["entra.principals"].resolve("g-eng-all", ResolveContext())
    one = len(tenant.headers_seen) - before

    resolvers["entra.principals_with_guests"].resolve("g-eng-all", ResolveContext())
    two = len(tenant.headers_seen) - before - one

    assert one == 1, "entra.principals must stay a single O(1) count"
    assert two == 2, "the breakdown pays for a second request, visibly"
