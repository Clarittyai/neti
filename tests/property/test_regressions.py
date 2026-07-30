"""Regressions for defects the property suite caught during Phase 1.

Both were silent-under-enforcement bugs, which is the failure direction that matters: the gate
returned a milder verdict than the operator declared, or lost the evidence for one.
"""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from neti.core.budget import BudgetRule, SessionTally, check_budgets
from neti.core.decide import decide, decide_arg, worst_tripped_band
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


def test_the_chain_survives_a_process_restart(tmp_path: Any) -> None:
    """Regression: a fresh Engine appending to an existing file broke the chain.

    `_last_digest` reset to None on construction, so the first record of every new process carried
    `prev_digest: null` in the middle of the file and `verify_chain` — correctly — called it a
    break. A break caused by a restart rather than by tampering is the worst possible false alarm
    for an audit surface: it teaches an operator to ignore the one signal that is supposed to be
    trustworthy.
    """
    from neti.core.record import build_record, verify_chain
    from neti.core.types import ProposedCall
    from neti.core.units import Unit
    from neti.store.jsonl import JsonlSink, chain_head, read_records

    path = tmp_path / "chain.ndjson"
    ceiling = Ceiling(unit=Unit.PRINCIPALS, bands=(Band(above=10, verdict=Verdict.BLOCK),))

    def session(magnitudes: list[int]) -> None:
        """One process lifetime: a new engine-like writer over the same file."""
        prev = chain_head(path)
        with JsonlSink(path) as sink:
            for m in magnitudes:
                decision = decide(
                    ProposedCall(tool="t"),
                    (("/g", "x", ceiling),),
                    {"/g": Resolution.resolved(Unit.PRINCIPALS, m)},
                )
                record = build_record(
                    decision,
                    decision_id=f"d-{m}",
                    decided_at="2026-07-30T00:00:00Z",
                    policy_digest="pol",
                    code_version="0.1.0",
                    prev_digest=prev,
                )
                sink.write(record)
                prev = record.record_digest

    session([1, 2])
    assert chain_head(path) is not None, "head must be readable between processes"
    session([3, 4])

    records = list(read_records(path))
    assert len(records) == 4
    assert [r.prev_digest for r in records[1:]].count(None) == 0, "a None appeared mid-chain"
    ok, bad = verify_chain(records)
    assert ok and bad is None


def test_chain_head_of_a_missing_file_is_none(tmp_path: Any) -> None:
    """A first run has no file, and that is not an error."""
    from neti.store.jsonl import chain_head

    assert chain_head(tmp_path / "nope.ndjson") is None
