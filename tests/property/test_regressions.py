"""Regressions for defects the property suite caught during Phase 1.

Both were silent-under-enforcement bugs, which is the failure direction that matters: the gate
returned a milder verdict than the operator declared, or lost the evidence for one.
"""

from __future__ import annotations

import json
from pathlib import Path
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
    assert check_budgets("send_email", (arg,), {"session": tally}, (rule,)).verdict is Verdict.BLOCK


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


# ---------------------------------------------------------------------------- the head sidecar
#
# `neti hook` is one process per tool call, and it read the *entire* record file twice on every
# one — once to seed the chain and once under the append lock. Measured on a lean install: 133ms
# on a fresh file, 273ms at ten thousand records, 816ms at fifty thousand. The README published a
# flat "p50 172ms", and the product's own advice is to run a week in observe mode, which is how you
# get to fifty thousand. A gate that becomes six times slower the longer you leave it on is a gate
# people uninstall.
#
# The sidecar makes it O(1), and these two tests are the pair that keeps it *safe* rather than
# merely fast: it must be used when it is current, and ignored the instant it is not.


def test_the_head_is_read_from_the_sidecar_rather_than_by_walking(tmp_path: Path) -> None:
    """Deterministic, not timed. `read_records` is the walk, so making it explode proves it is
    not being called — a timing assertion would be flaky and would prove less."""
    from neti.store import jsonl

    records = tmp_path / "d.ndjson"
    sink = jsonl.JsonlSink(records)
    try:
        written = sink.write(_a_record())
    finally:
        sink.close()

    def explode(*_a: object, **_k: object) -> object:
        raise AssertionError("chain_head walked the file instead of reading the sidecar")

    original = jsonl.read_records
    jsonl.read_records = explode  # type: ignore[assignment]
    try:
        assert jsonl.chain_head(records) == written.record_digest
    finally:
        jsonl.read_records = original  # type: ignore[assignment]


def test_a_sidecar_that_no_longer_describes_the_file_is_ignored(tmp_path: Path) -> None:
    """The half that makes the optimisation safe to have at all.

    The cache is keyed on the records file's byte length, so anything that appended, truncated or
    rewrote the file outside the sink stops it matching and every reader falls back to the walk. A
    head cache that could go stale *and be believed* would seal the next record against the wrong
    predecessor and fork the chain — which is the one failure this file exists to prevent.
    """
    from neti.core.record import verify_chain
    from neti.store import jsonl

    records = tmp_path / "d.ndjson"
    sink = jsonl.JsonlSink(records)
    try:
        first = sink.write(_a_record())
    finally:
        sink.close()

    # A plausible, wrong sidecar: right shape, stale digest, and a length that no longer matches.
    (tmp_path / "d.ndjson.head").write_text(
        json.dumps({"bytes": 1, "digest": "0" * 64}), encoding="utf-8"
    )
    assert jsonl.chain_head(records) == first.record_digest, "a stale sidecar was believed"

    # And a second write still chains correctly off the real head rather than the bogus one.
    sink = jsonl.JsonlSink(records)
    try:
        second = sink.write(_a_record())
    finally:
        sink.close()
    assert second.prev_digest == first.record_digest

    ok, bad = verify_chain(list(jsonl.read_records(records)))
    assert ok, f"chain broke at {bad}"


def test_a_corrupt_or_missing_sidecar_costs_a_walk_and_nothing_else(tmp_path: Path) -> None:
    """It fails to *slow*, never to wrong. Garbage and absent take the same path."""
    from neti.store import jsonl

    records = tmp_path / "d.ndjson"
    sink = jsonl.JsonlSink(records)
    try:
        written = sink.write(_a_record())
    finally:
        sink.close()

    head = tmp_path / "d.ndjson.head"
    for content in ("", "not json", "[]", '{"bytes": "x"}'):
        head.write_text(content, encoding="utf-8")
        assert jsonl.chain_head(records) == written.record_digest, f"broke on {content!r}"
    head.unlink()
    assert jsonl.chain_head(records) == written.record_digest


def _a_record() -> Any:
    from neti.core.record import DecisionRecord

    return DecisionRecord.model_validate(
        {
            "decision_id": "d",
            "decided_at": "2026-01-01T00:00:00+00:00",
            "tool": "Read",
            "args": {"file_path": "/tmp/x"},
            "verdict": "allow",
            "rule": "under_all_bands",
            "mode": "observe",
            "policy_digest": "p",
            "code_version": "0.1.0",
        }
    )
