"""Invariant 8: a proposal derived from traffic must be able to catch that traffic's own outliers.

`neti propose` turns observed calls into the ceilings a customer commits. It is the one algorithm
here whose output becomes somebody else's security posture, and it was tested only on clean
unimodal distributions or on corpora below `MIN_SAMPLES` — never on the shape it exists for.

Fed a realistic one — 32 sends of 25 recipients, 4 of 500, 4 of 41,203 — it proposed

    confirm above 100,000
    block   above 500,000

twelve times the largest call ever made. It could never fire, it caught none of the outliers, and no
warning appeared, because the only guard triggered when the maximum was *above* the ceiling and
nothing was. The anchor was p95, which is right while the tail is genuinely a tail; past 5% of
traffic the outliers *are* the p95 and multiplying it walks the ceiling past everything observed.

Examples could not have found that — the failure needs a specific ratio of outliers to normal work,
and nobody writes that example until after they have been burned. Properties can. Hypothesis is
already a dependency and already used on the core; this points it at the surface.
"""

from __future__ import annotations

import statistics

from hypothesis import given, settings
from hypothesis import strategies as st

from neti.insight.propose import MIN_SAMPLES, SPREAD, Proposal, propose
from neti.insight.report import Distribution, ReportSummary


def summary_of(magnitudes: list[int]) -> ReportSummary:
    return ReportSummary(
        distributions={
            ("send_email", "/to"): Distribution(
                tool="send_email", pointer="/to", unit="recipients", magnitudes=magnitudes
            )
        }
    )


def only(magnitudes: list[int]) -> Proposal:
    return propose(summary_of(magnitudes))[0]


# Enough calls to be proposed on at all. Magnitudes stay positive: a resolved magnitude is a
# cardinality, and zero-or-negative is not a thing the resolvers can produce.
enough = st.lists(st.integers(1, 5_000_000), min_size=MIN_SAMPLES, max_size=300)


@given(magnitudes=enough)
@settings(max_examples=200, deadline=None)
def test_a_proposal_is_internally_coherent(magnitudes: list[int]) -> None:
    """Whatever the distribution: block is not below confirm, and neither is zero or negative."""
    p = only(magnitudes)
    if not p.actionable:
        return
    assert p.confirm_above is not None and p.block_above is not None
    assert 0 < p.confirm_above <= p.block_above


@given(magnitudes=enough)
@settings(max_examples=200, deadline=None)
def test_bimodal_traffic_is_always_caught_by_its_own_proposal(magnitudes: list[int]) -> None:
    """**The property whose violation produced `block above 500,000`.**

    If the traffic plainly contains outliers — a maximum an order of magnitude above the median —
    then a proposal derived from that traffic has to stop or question at least one of the calls it
    was derived from. A ceiling above everything ever observed is not a conservative proposal, it is
    a broken one.
    """
    p = only(magnitudes)
    if not p.actionable:
        return

    median = max(int(statistics.median(magnitudes)), 1)
    if max(magnitudes) <= median * SPREAD:
        return  # one population; a ceiling above the maximum is the right answer there

    assert p.would_block + p.would_confirm > 0, (
        f"bimodal traffic (median {median:,}, max {max(magnitudes):,}) got "
        f"confirm={p.confirm_above:,} block={p.block_above:,}, which catches nothing it has seen"
    )


@given(magnitudes=enough)
@settings(max_examples=200, deadline=None)
def test_the_impact_counts_match_a_recount(magnitudes: list[int]) -> None:
    """The impact line is the only part an operator checks before committing. If it disagrees with
    the magnitudes it was computed from, every other number on the screen is unverifiable."""
    p = only(magnitudes)
    if not p.actionable:
        return
    assert p.confirm_above is not None and p.block_above is not None

    expected_block = sum(1 for m in magnitudes if m > p.block_above)
    expected_confirm = sum(1 for m in magnitudes if p.confirm_above < m <= p.block_above)
    assert (p.would_block, p.would_confirm) == (expected_block, expected_confirm)


@given(magnitudes=enough)
@settings(max_examples=200, deadline=None)
def test_the_displayed_anchor_names_the_value_actually_used(magnitudes: list[int]) -> None:
    """It once printed `p95=25` while the real p95 was 41,203 — a lie in the one output an operator
    is meant to check the arithmetic of."""
    dist = summary_of(magnitudes).distributions[("send_email", "/to")]
    p = only(magnitudes)
    assert p.anchor in {"p50", "p95"}
    assert p.normal == max(dist.p50 if p.anchor == "p50" else dist.p95, 1)


@given(magnitudes=st.lists(st.integers(1, 100_000), min_size=1, max_size=MIN_SAMPLES - 1))
@settings(max_examples=100, deadline=None)
def test_below_the_threshold_it_never_proposes_a_number(magnitudes: list[int]) -> None:
    """A ceiling fitted to a handful of calls encodes the accident of that week, and looks
    configured while doing it."""
    p = only(magnitudes)
    assert not p.actionable
    assert p.confirm_above is None and p.block_above is None
    assert str(MIN_SAMPLES) in p.rationale


@given(value=st.integers(1, 1_000_000), n=st.integers(MIN_SAMPLES, 200))
@settings(max_examples=100, deadline=None)
def test_uniform_traffic_gets_a_ceiling_above_itself(value: int, n: int) -> None:
    """The behaviour the first attempt at the bimodal fix broke, so it is pinned as a property.

    With no spread there are no outliers, and a ceiling above the observed maximum is exactly right:
    it binds only on behaviour that has not happened yet. "Block is above the max" is therefore not
    the test for a broken proposal — "the traffic has two populations and this catches neither" is.
    """
    p = only([value] * n)
    assert p.actionable
    assert p.block_above is not None and p.block_above > value
    assert p.would_block == 0 and p.would_confirm == 0


@given(magnitudes=enough)
@settings(max_examples=100, deadline=None)
def test_a_proposal_never_blocks_everything(magnitudes: list[int]) -> None:
    """A ceiling below the median stops ordinary work, and an operator who commits one turns the
    gate off within a day. Whatever the distribution, normal traffic has to survive."""
    p = only(magnitudes)
    if not p.actionable:
        return
    assert p.would_block < len(magnitudes), "a proposal that blocks every observed call is useless"
