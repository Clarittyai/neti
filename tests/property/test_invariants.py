"""The invariants from DECISION.md, as tests.

These are load-bearing. Invariant 1 (monotonicity) is the one that would have caught the defect that
killed the predecessor design, where combining evidence with a `meet` let an extra parameter turn a
BLOCK into an ALLOW.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from neti.core.budget import BudgetRule, SessionTally, check_budgets
from neti.core.decide import decide, decide_arg
from neti.core.types import ArgDecision, Band, Ceiling, ProposedCall, Resolution
from neti.core.units import Direction, Unit
from neti.core.verdict import Mode, ResolutionState, Verdict

# Bands with distinct thresholds and non-decreasing severity as the threshold grows — the shape any
# sane policy has, and the shape `Ceiling` sorts into.
band_thresholds = st.lists(st.integers(0, 10_000), min_size=1, max_size=4, unique=True)


@st.composite
def ceilings(draw: st.DrawFn) -> Ceiling:
    thresholds = sorted(draw(band_thresholds))
    verdicts = draw(
        st.lists(
            st.sampled_from([Verdict.FLAG, Verdict.CONFIRM, Verdict.BLOCK]),
            min_size=len(thresholds),
            max_size=len(thresholds),
        )
    )
    # severity must not decrease as the threshold rises, or "most severe applicable" is meaningless
    monotone = []
    running = Verdict.FLAG
    for v in verdicts:
        running = max(running, v)
        monotone.append(running)
    return Ceiling(
        unit=Unit.PRINCIPALS,
        bands=tuple(Band(above=t, verdict=v) for t, v in zip(thresholds, monotone, strict=True)),
        on_unresolved=draw(st.sampled_from([Verdict.CONFIRM, Verdict.BLOCK])),
        on_unbounded=draw(st.sampled_from([Verdict.CONFIRM, Verdict.BLOCK])),
    )


# --------------------------------------------------------------- 1. monotone


@given(ceiling=ceilings(), a=st.integers(0, 20_000), b=st.integers(0, 20_000))
def test_raising_magnitude_never_lowers_severity(ceiling: Ceiling, a: int, b: int) -> None:
    lo, hi = min(a, b), max(a, b)
    v_lo = decide_arg("/g", "t", ceiling, Resolution.resolved(Unit.PRINCIPALS, lo)).verdict
    v_hi = decide_arg("/g", "t", ceiling, Resolution.resolved(Unit.PRINCIPALS, hi)).verdict
    assert v_hi >= v_lo, f"magnitude {hi} was more permissive than {lo}"


@given(
    ceiling=ceilings(),
    magnitudes=st.lists(st.integers(0, 20_000), min_size=1, max_size=5),
    extra=st.integers(0, 20_000),
)
def test_adding_a_gated_arg_never_lowers_severity(
    ceiling: Ceiling, magnitudes: list[int], extra: int
) -> None:
    """Adding a parameter to a call can only tighten the verdict. This is the join property."""
    call = ProposedCall(tool="t")

    def run(ms: list[int]) -> Verdict:
        gated = tuple((f"/p{i}", "t", ceiling) for i in range(len(ms)))
        res = {f"/p{i}": Resolution.resolved(Unit.PRINCIPALS, m) for i, m in enumerate(ms)}
        return decide(call, gated, res).verdict

    assert run([*magnitudes, extra]) >= run(magnitudes)


# --------------------------------------------------------------- 2. no silent allow on ignorance


@given(
    ceiling=ceilings(),
    state=st.sampled_from([ResolutionState.UNRESOLVED, ResolutionState.PARTIAL]),
)
def test_ignorance_never_allows(ceiling: Ceiling, state: ResolutionState) -> None:
    res = (
        Resolution.unresolved(Unit.PRINCIPALS, "timeout")
        if state is ResolutionState.UNRESOLVED
        else Resolution.partial(Unit.PRINCIPALS, "truncated")
    )
    d = decide_arg("/g", "t", ceiling, res)
    assert d.verdict is ceiling.on_unresolved
    assert d.verdict > Verdict.ALLOW, "declared on_unresolved must not be permissive in these tests"


def test_missing_resolution_is_not_an_absent_constraint() -> None:
    """A gated pointer with no resolution supplied is a bug, and must fail closed."""
    ceiling = Ceiling(unit=Unit.PRINCIPALS, bands=(Band(above=10, verdict=Verdict.BLOCK),))
    d = decide(ProposedCall(tool="t"), (("/group", "x", ceiling),), {})
    assert d.verdict is Verdict.BLOCK
    assert d.args[0].resolution.state is ResolutionState.UNRESOLVED
    assert d.args[0].resolution.evidence["reason"] == "no_resolution_supplied"


# --------------------------------------------------------------- 3. direction respected


@given(ceiling=ceilings(), magnitude=st.integers(0, 20_000))
def test_lower_bound_never_allows(ceiling: Ceiling, magnitude: int) -> None:
    """true >= measured, so being under the ceiling is never conclusive."""
    d = decide_arg(
        "/g",
        "t",
        ceiling,
        Resolution.resolved(Unit.PRINCIPALS, magnitude, direction=Direction.LOWER_BOUND),
    )
    assert d.verdict > Verdict.ALLOW


@given(ceiling=ceilings(), magnitude=st.integers(0, 20_000))
def test_upper_bound_block_is_always_flagged_as_possibly_over_blocking(
    ceiling: Ceiling, magnitude: int
) -> None:
    """true <= measured, so being over the ceiling is not conclusive — the block still stands."""
    d = decide_arg(
        "/g",
        "t",
        ceiling,
        Resolution.resolved(Unit.PRINCIPALS, magnitude, direction=Direction.UPPER_BOUND),
    )
    if d.verdict > Verdict.ALLOW and d.tripped is not None:
        assert d.over_block_possible
    else:
        assert not d.over_block_possible


@given(ceiling=ceilings(), magnitude=st.integers(0, 20_000))
def test_exact_is_never_flagged(ceiling: Ceiling, magnitude: int) -> None:
    d = decide_arg("/g", "t", ceiling, Resolution.resolved(Unit.PRINCIPALS, magnitude))
    assert not d.over_block_possible


# --------------------------------------------------------------- resolution model


@given(magnitude=st.integers(0, 1000))
def test_non_resolved_cannot_carry_a_magnitude(magnitude: int) -> None:
    """A truncated or failed count must never be readable as a number.

    See RESOLVER_CONTRACT.md rule 2.
    """
    import pytest
    from pydantic import ValidationError

    for state in (ResolutionState.PARTIAL, ResolutionState.UNRESOLVED):
        with pytest.raises(ValidationError):
            Resolution(state=state, unit=Unit.PRINCIPALS, magnitude=magnitude)


def test_resolved_must_carry_a_magnitude() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Resolution(state=ResolutionState.RESOLVED, unit=Unit.PRINCIPALS)


# --------------------------------------------------------------- session budgets (NC-01)


def test_four_thousand_single_recipient_sends_trip_the_budget() -> None:
    """The NC-01 regression test.

    Per-call resolution sees a magnitude of 1 four thousand times and every per-call ceiling passes.
    Only the declared cumulative budget catches it. If this test ever goes green for the wrong
    reason, the whole session-budget mechanism has silently stopped working.
    """
    rules = (
        BudgetRule(
            tools=frozenset({"send_email"}),
            unit=Unit.RECIPIENTS,
            bands=(
                Band(above=200, verdict=Verdict.CONFIRM),
                Band(above=1000, verdict=Verdict.BLOCK),
            ),
        ),
    )
    per_call_ceiling = Ceiling(
        unit=Unit.RECIPIENTS, bands=(Band(above=500, verdict=Verdict.BLOCK),)
    )
    tally = SessionTally()
    first_confirm: int | None = None
    first_block: int | None = None

    for i in range(1, 4001):
        res = Resolution.resolved(Unit.RECIPIENTS, 1)
        arg = decide_arg("/to", f"user{i}@example.com", per_call_ceiling, res)
        assert arg.verdict is Verdict.ALLOW, "per-call gate is structurally blind here"

        budget = check_budgets("send_email", (arg,), tally, rules)
        if budget.verdict is Verdict.CONFIRM and first_confirm is None:
            first_confirm = i
        if budget.verdict is Verdict.BLOCK and first_block is None:
            first_block = i
            break
        tally = tally.add_committed((arg,))

    assert first_confirm == 201
    assert first_block == 1001


def test_blocked_calls_do_not_consume_budget() -> None:
    """A blocked attempt must not poison the rest of the session."""
    tally = SessionTally().add(Unit.RECIPIENTS, 100)
    arg = ArgDecision(
        pointer="/to",
        target="t",
        verdict=Verdict.BLOCK,
        resolution=Resolution.resolved(Unit.RECIPIENTS, 5000),
        rule="magnitude>500",
    )
    rules = (
        BudgetRule(
            tools=frozenset({"send_email"}),
            unit=Unit.RECIPIENTS,
            bands=(Band(above=1000, verdict=Verdict.BLOCK),),
        ),
    )
    assert check_budgets("send_email", (arg,), tally, rules).verdict is Verdict.BLOCK
    # the caller did not commit, so the tally is untouched
    assert tally.total(Unit.RECIPIENTS) == 100


def test_unresolved_contributes_nothing_to_the_running_total() -> None:
    """Inventing a contribution would make the total fiction; on_unresolved already handled it."""
    tally = SessionTally()
    arg = ArgDecision(
        pointer="/to",
        target="t",
        verdict=Verdict.BLOCK,
        resolution=Resolution.unresolved(Unit.RECIPIENTS, "timeout"),
        rule="on_unresolved:unresolved",
    )
    assert tally.add_committed((arg,)).total(Unit.RECIPIENTS) == 0


# --------------------------------------------------------------- mode


@settings(max_examples=50)
@given(ceiling=ceilings(), magnitude=st.integers(0, 20_000))
def test_observe_mode_always_proceeds(ceiling: Ceiling, magnitude: int) -> None:
    """Observe mode is what makes installing neti reversible. It must never withhold a call."""
    d = decide(
        ProposedCall(tool="t"),
        (("/g", "t", ceiling),),
        {"/g": Resolution.resolved(Unit.PRINCIPALS, magnitude)},
        mode=Mode.OBSERVE,
    )
    assert d.proceeds
