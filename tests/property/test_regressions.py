"""Regressions for defects the property suite caught during Phase 1.

Both were silent-under-enforcement bugs, which is the failure direction that matters: the gate
returned a milder verdict than the operator declared, or lost the evidence for one.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from neti.core.budget import BudgetRule, SessionTally, check_budgets
from neti.core.decide import decide_arg, worst_tripped_band
from neti.core.types import ArgDecision, Band, Ceiling, Resolution
from neti.core.units import Unit
from neti.core.verdict import Verdict


@given(
    magnitude=st.integers(0, 5000),
    bands=st.lists(
        st.tuples(
            st.integers(0, 4000),
            st.sampled_from([Verdict.FLAG, Verdict.CONFIRM, Verdict.BLOCK]),
        ),
        min_size=1,
        max_size=5,
        unique_by=lambda t: t[0],
    ),
    perm=st.randoms(use_true_random=False),
)
def test_band_selection_is_independent_of_input_order(
    magnitude: int, bands: list[tuple[int, Verdict]], perm: object
) -> None:
    """The original bug: `first_tripped_band` walked the tuple and returned the first breach, so an
    unsorted band list yielded the mildest applicable verdict instead of the most severe."""
    made = [Band(above=a, verdict=v) for a, v in bands]
    shuffled = list(made)
    perm.shuffle(shuffled)  # type: ignore[attr-defined]

    a = worst_tripped_band(magnitude, tuple(made))
    b = worst_tripped_band(magnitude, tuple(shuffled))
    assert a == b


def test_unsorted_budget_bands_still_block() -> None:
    """Regression: BudgetRule had no sort validator, so a BLOCK band declared after a CONFIRM band
    was unreachable and a 1001-recipient session reported CONFIRM."""
    rule = BudgetRule(
        tools=frozenset({"send_email"}),
        unit=Unit.RECIPIENTS,
        # deliberately declared ascending, the order a human would naturally write
        bands=(
            Band(above=200, verdict=Verdict.CONFIRM),
            Band(above=1000, verdict=Verdict.BLOCK),
        ),
    )
    assert [b.above for b in rule.bands] == [1000, 200], "bands should be stored descending"

    arg = ArgDecision(
        pointer="/to",
        target="x",
        verdict=Verdict.ALLOW,
        resolution=Resolution.resolved(Unit.RECIPIENTS, 1),
        rule="under_all_bands",
    )
    tally = SessionTally(totals={"recipients": 1000})
    assert check_budgets("send_email", (arg,), tally, (rule,)).verdict is Verdict.BLOCK


def test_every_breach_is_recorded_not_just_the_deciding_one() -> None:
    """Regression: only the winning band was kept, so a total that was also over the ceiling
    vanished from the record behind a more severe breakdown breach."""
    ceiling = Ceiling(
        unit=Unit.RECIPIENTS,
        bands=(Band(above=100, verdict=Verdict.CONFIRM),),
        breakdown_bands={"guest": (Band(above=50, verdict=Verdict.BLOCK),)},
    )
    d = decide_arg(
        "/to",
        "all-customers@acme.com",
        ceiling,
        Resolution.resolved(Unit.RECIPIENTS, 500, breakdown={"internal": 20, "guest": 480}),
    )
    assert d.verdict is Verdict.BLOCK  # the guest breach decides
    sources = {b.source: (b.observed, b.above) for b in d.breaches}
    assert sources == {"magnitude": (500, 100), "breakdown:guest": (480, 50)}


def test_breach_order_is_stable() -> None:
    """Breaches are sorted by source, so the record digest cannot depend on dict iteration order."""
    ceiling = Ceiling(
        unit=Unit.RECIPIENTS,
        bands=(Band(above=10, verdict=Verdict.FLAG),),
        breakdown_bands={
            "zeta": (Band(above=1, verdict=Verdict.FLAG),),
            "alpha": (Band(above=1, verdict=Verdict.FLAG),),
        },
    )
    d = decide_arg(
        "/to",
        "x",
        ceiling,
        Resolution.resolved(Unit.RECIPIENTS, 100, breakdown={"zeta": 9, "alpha": 9}),
    )
    assert [b.source for b in d.breaches] == ["breakdown:alpha", "breakdown:zeta", "magnitude"]


def test_band_above_is_exclusive() -> None:
    """A magnitude exactly equal to the ceiling is allowed: `above: 200` means 201 trips it."""
    ceiling = Ceiling(unit=Unit.PRINCIPALS, bands=(Band(above=200, verdict=Verdict.BLOCK),))
    assert (
        decide_arg("/g", "x", ceiling, Resolution.resolved(Unit.PRINCIPALS, 200)).verdict
        is Verdict.ALLOW
    )
    assert (
        decide_arg("/g", "x", ceiling, Resolution.resolved(Unit.PRINCIPALS, 201)).verdict
        is Verdict.BLOCK
    )
