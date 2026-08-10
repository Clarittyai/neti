"""Budgets over windows wider than one conversation.

A `session` budget answers *"did this agent go haywire in one run?"* It cannot answer *"has this
agent been quietly reading everything for three days?"*, because each new conversation starts the
total at zero. That second shape is `glean-bulk-download` in the incident corpus — 8,000,000 objects
accumulated across many retrievals, published as a miss precisely because per-call resolution and
per-session totals are both blind to it.

`window: day`, `window: week` and `window: rolling:<n>h` key the same sidecar by time instead of by
conversation. These tests drive the clock directly rather than waiting for one, which is possible
only because `SessionStore` takes `now` as an argument — the same property that keeps a recorded
decision replayable.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from neti.config.policy import Policy
from neti.core.budget import MAX_ROLLING_HOURS, SessionTally, Window, WindowKind, check_budgets
from neti.core.types import ArgDecision, ProposedCall, Resolution
from neti.core.units import Unit
from neti.core.verdict import Mode, Verdict
from neti.engine import Engine
from neti.resolvers.filesystem import FilesystemResolver
from neti.store.sessions import MemoryTallies, SessionStore, bucket_key

HOUR = 3600.0
DAY = 24 * HOUR

# 2025-10-09T11:00Z, a Thursday. Adding a day crosses neither the ISO week (both W41) nor the
# month, which is what the rollover assertions below depend on. Chosen rather than picked: a
# fixture that silently straddles two buckets makes a passing test meaningless — and the date is
# stated because it was written as "a Friday" first, and a comment nobody checks is a comment.
NOON = 1_760_007_600.0


def one(magnitude: int = 1) -> tuple[ArgDecision, ...]:
    return (
        ArgDecision(
            pointer="/file_path",
            target="a.ts",
            verdict=Verdict.ALLOW,
            resolution=Resolution.resolved(Unit.OBJECTS, magnitude),
            rule="under_all_bands",
        ),
    )


def stores(tmp_path: Path) -> list[object]:
    """Both implementations, because they must agree.

    `neti hook` is one process per call and needs the sidecar; `neti gate` is one long-lived process
    and does not. A window that rolled over on disk but not in memory would be a gate whose
    behaviour depended on which door the call arrived through, which is the one thing this product
    does not allow.
    """
    return [SessionStore(tmp_path / "out" / "decisions.ndjson"), MemoryTallies()]


# --------------------------------------------------------------- parsing: dead config is an error


@pytest.mark.parametrize(
    "raw", ["dayly", "daily", "rolling:0h", "rolling:169h", "rolling:24", "rolling:h", "hour", "1d"]
)
def test_an_unreadable_window_is_refused_rather_than_defaulted(raw: str) -> None:
    """`window: dayly` used to be accepted in silence and counted per conversation forever.

    The operator had every reason to believe a daily budget was running. That is the dead-config
    failure this project keeps finding, and the only fix that holds is refusing to guess.
    """
    with pytest.raises(ValueError, match="window"):
        Window.parse(raw)


@pytest.mark.parametrize(
    ("raw", "kind", "hours"),
    [
        ("session", WindowKind.SESSION, 0),
        ("day", WindowKind.DAY, 0),
        ("week", WindowKind.WEEK, 0),
        ("rolling:1h", WindowKind.ROLLING, 1),
        ("rolling:24h", WindowKind.ROLLING, 24),
        (f"rolling:{MAX_ROLLING_HOURS}h", WindowKind.ROLLING, MAX_ROLLING_HOURS),
    ],
)
def test_a_window_round_trips_to_the_spelling_it_was_declared_with(
    raw: str, kind: WindowKind, hours: int
) -> None:
    """The serialised form goes into `Policy.digest()`, which is stamped into every record.

    If a window did not round-trip, two agents on one config would record two different policies —
    the defect already found once, on `frozenset` tools serialising in hash order.
    """
    window = Window.parse(raw)
    assert (window.kind, window.hours) == (kind, hours)
    assert str(window) == raw


def test_an_absent_window_is_a_session_window() -> None:
    """Every policy written before windows existed declared nothing, and must keep its meaning."""
    assert Window.parse("") == Window() == Window(kind=WindowKind.SESSION)


# --------------------------------------------------------------- bucketing


def test_a_session_window_keeps_the_filename_it_always_had(tmp_path: Path) -> None:
    """An install that upgrades into windows must not silently reset the totals it was keeping.

    The bucket key for a session is the same sanitised session id the store used before windows
    existed, so yesterday's sidecar is still the one read today.
    """
    assert bucket_key(Window(), "abc123", NOON) == "abc123"
    # Still sanitised: a session id is agent-supplied and this becomes a filename. Dots survive,
    # separators do not, so the result cannot climb out of the directory.
    assert bucket_key(Window(), "../../../etc/passwd", NOON) == ".._.._.._etc_passwd"


def test_time_buckets_are_shared_across_sessions_and_keyed_in_utc() -> None:
    """Spanning conversations is the entire reason to declare a day window."""
    day = Window.parse("day")
    assert bucket_key(day, "session-a", NOON) == bucket_key(day, "session-b", NOON)
    assert bucket_key(day, "s", NOON) != bucket_key(day, "s", NOON + DAY)
    assert bucket_key(day, "s", NOON).startswith("day-")
    # A rolling bucket is one file whatever the hour: it holds sub-totals rather than being one.
    rolling = Window.parse("rolling:24h")
    assert bucket_key(rolling, "s", NOON) == bucket_key(rolling, "s", NOON + 5 * HOUR)


# --------------------------------------------------------------- the windows themselves


@pytest.mark.parametrize("index", [0, 1])
def test_a_day_total_spans_sessions_and_resets_at_the_boundary(tmp_path: Path, index: int) -> None:
    store = stores(tmp_path)[index]
    day = Window.parse("day")

    store.add(day, "morning-conversation", NOON, one())
    store.add(day, "afternoon-conversation", NOON + 4 * HOUR, one())
    assert store.load(day, "a-third-conversation", NOON + 5 * HOUR).total(Unit.OBJECTS) == 2

    # The next day is a different bucket, and starts empty.
    assert store.load(day, "morning-conversation", NOON + DAY).total(Unit.OBJECTS) == 0


@pytest.mark.parametrize("index", [0, 1])
def test_a_rolling_window_forgets_the_hour_that_falls_out(tmp_path: Path, index: int) -> None:
    """The property a calendar window cannot have, and the reason `rolling:` exists.

    A `day` budget of 20,000 permits 40,000 across a single midnight. A rolling window has no
    boundary to straddle: the twenty-fifth hour is dropped as the twenty-fifth hour arrives.
    """
    store = stores(tmp_path)[index]
    rolling = Window.parse("rolling:3h")

    for hour in range(3):
        store.add(rolling, "s", NOON + hour * HOUR, one())
    assert store.load(rolling, "s", NOON + 2 * HOUR).total(Unit.OBJECTS) == 3

    # One hour later the oldest of the three is outside the window.
    assert store.load(rolling, "s", NOON + 3 * HOUR).total(Unit.OBJECTS) == 2
    # Four hours later, nothing is.
    assert store.load(rolling, "s", NOON + 6 * HOUR).total(Unit.OBJECTS) == 0


@pytest.mark.parametrize("index", [0, 1])
def test_a_clock_that_jumps_backwards_cannot_zero_a_budget(tmp_path: Path, index: int) -> None:
    """An NTP correction, or a laptop waking in another timezone.

    Sub-totals stamped in the future are kept rather than discarded. Over-counting costs a
    confirmation; dropping them would hand anyone who can move the clock a way to reset the budget.
    """
    store = stores(tmp_path)[index]
    rolling = Window.parse("rolling:3h")

    store.add(rolling, "s", NOON + 10 * HOUR, one())
    assert store.load(rolling, "s", NOON).total(Unit.OBJECTS) == 1


def test_a_day_total_survives_a_restart(tmp_path: Path) -> None:
    """The sidecar is the whole point: `neti hook` is a fresh process per tool call."""
    records = tmp_path / "out" / "decisions.ndjson"
    day = Window.parse("day")

    SessionStore(records).add(day, "s1", NOON, one())
    SessionStore(records).add(day, "s2", NOON + HOUR, one())
    assert SessionStore(records).load(day, "s3", NOON + 2 * HOUR).total(Unit.OBJECTS) == 2


# --------------------------------------------------------------- two windows at once


def two_window_policy() -> Policy:
    """A tight session ceiling and a wider daily one — the realistic declaration.

    `block above 3 this conversation` catches a run that goes wrong; `block above 5 today` catches
    the patient version that opens a new conversation each time.
    """
    return Policy.model_validate(
        {
            "version": 1,
            "mode": Mode.ENFORCE,
            "session_budgets": [
                {
                    "tools": frozenset({"Read"}),
                    "unit": "objects",
                    "bands": ({"above": 3, "verdict": "block"},),
                    "window": "session",
                },
                {
                    "tools": frozenset({"Read"}),
                    "unit": "objects",
                    "bands": ({"above": 5, "verdict": "block"},),
                    "window": "day",
                },
            ],
            "tools": {"Read": {"gate": {"/file_path": {"resolver": "fs.paths"}}}},
        }
    )


def test_two_windows_keep_separate_totals_and_neither_sees_the_other() -> None:
    """One call counts into both. A `day` total must not satisfy a `session` ceiling."""
    rules = two_window_policy().session_budgets
    tallies = {
        "session": SessionTally(totals={"objects": 1}),
        "day": SessionTally(totals={"objects": 5}),
    }

    decision = check_budgets("Read", one(), tallies, rules)
    assert decision.verdict is Verdict.BLOCK
    assert decision.running_total == 6, "the day total is what tripped, not the session's 2"
    assert decision.rule == "objects_total>5@day", "a fired budget names its window"


def test_a_session_budget_still_names_itself_the_way_it_always_did() -> None:
    """Records written before windows existed must keep re-deriving to the same string."""
    rules = two_window_policy().session_budgets[:1]
    tallies = {"session": SessionTally(totals={"objects": 3})}
    assert check_budgets("Read", one(), tallies, rules).rule == "objects_total>3"


def test_a_window_with_no_loaded_tally_counts_as_empty() -> None:
    """Under-counting is the direction the whole store degrades in. It never over-blocks."""
    rules = two_window_policy().session_budgets
    assert check_budgets("Read", one(), {}, rules).verdict is Verdict.ALLOW


# --------------------------------------------------------------- end to end, through the engine


def test_a_daily_budget_fires_across_separate_sessions(tmp_path: Path) -> None:
    """The `glean-bulk-download` shape, on the integration it would actually happen through.

    Six reads of one object each, a different conversation every time, one fresh engine per call —
    which is exactly what `neti hook` does. A session budget cannot see this. A daily one can.
    """
    for i in range(20):
        (tmp_path / f"f{i}.ts").write_text("x", encoding="utf-8")
    records = tmp_path / "out" / "decisions.ndjson"

    verdicts = []
    for i in range(7):
        engine = Engine(
            policy=two_window_policy(),
            resolvers={"fs.paths": FilesystemResolver(root=tmp_path)},
            sessions=SessionStore(records),
        )
        result = engine.gate(
            ProposedCall(
                tool="Read",
                args={"file_path": str(tmp_path / f"f{i}.ts")},
                session_id=f"conversation-{i}",
            )
        )
        verdicts.append(result.decision.verdict)

    assert [v.name for v in verdicts[:5]] == ["ALLOW"] * 5
    assert verdicts[5] is Verdict.BLOCK, "the sixth object crosses the declared daily ceiling of 5"
    assert verdicts[6] is Verdict.BLOCK, "and it stays crossed"


def test_a_blocked_call_does_not_consume_a_windowed_budget(tmp_path: Path) -> None:
    """The rule that governs all session state: budget is spent by calls that ran.

    A blocked attempt that still spent budget would let one rejected call poison the rest of the
    window — and a *day* is a long time to be poisoned for.
    """
    for i in range(20):
        (tmp_path / f"f{i}.ts").write_text("x", encoding="utf-8")
    records = tmp_path / "out" / "decisions.ndjson"
    day = Window.parse("day")

    def gate(index: int) -> Verdict:
        engine = Engine(
            policy=two_window_policy(),
            resolvers={"fs.paths": FilesystemResolver(root=tmp_path)},
            sessions=SessionStore(records),
        )
        return engine.gate(
            ProposedCall(
                tool="Read",
                args={"file_path": str(tmp_path / f"f{index}.ts")},
                session_id=f"c-{index}",
            )
        ).decision.verdict

    for i in range(6):
        gate(i)
    # The engine reads the real clock, so the bucket to inspect is today's — not `NOON`, which is a
    # fixture for the store-level tests above. Reading the wrong bucket here returned 0 and looked
    # exactly like the assertion passing for the right reason.
    stamp = time.time()
    total = SessionStore(records).load(day, "c-0", stamp).total(Unit.OBJECTS)
    for i in range(6, 10):
        assert gate(i) is Verdict.BLOCK
    after = SessionStore(records).load(day, "c-0", stamp).total(Unit.OBJECTS)

    assert after == total == 5, "four blocked calls added nothing to the day's total"


# --------------------------------------------------------------- proposing a budget


def _record(session: str, day: str, magnitude: int, verdict: str = "allow") -> object:
    """A decision record with just the fields `build_report` reads for bucket totals."""
    from neti.core.record import DecisionRecord

    return DecisionRecord(
        decision_id=f"{session}-{day}-{magnitude}",
        session_id=session,
        decided_at=f"{day}T12:00:00+00:00",
        tool="Read",
        verdict=verdict,
        rule="r",
        mode="observe",
        causes=(
            {
                "pointer": "/file_path",
                "unit": "objects",
                "magnitude": magnitude,
                "direction": "exact",
            },
        ),
        policy_digest="d",
        code_version="v",
    )


def test_a_budget_is_proposed_from_whole_days_not_from_single_calls() -> None:
    """The number a budget needs is not in the per-call distribution at all.

    Six days of a hundred one-object reads each: every call is magnitude 1, so a per-call ceiling
    has nothing to say. The day totals are 100, and that is the number a budget is declared against.
    """
    from neti.insight.propose import propose_budgets
    from neti.insight.report import build_report

    days = [f"2026-08-{n:02d}" for n in range(1, 7)]
    records = [_record(f"s-{day}", day, 1) for day in days for _ in range(100)]
    summary = build_report(records)

    assert summary.bucket_totals[("day", "objects")] == dict.fromkeys(days, 100)

    daily = next(p for p in propose_budgets(summary) if p.window == "day")
    assert daily.buckets == 6
    assert daily.median == 100
    assert daily.actionable
    assert (daily.confirm_above, daily.block_above) == (200, 500)
    assert daily.would_trip == 0, "nothing observed crosses it; it binds on the unseen"


def test_a_budget_refuses_to_propose_from_too_few_windows() -> None:
    """The same refusal the per-call path makes, counted in windows rather than in calls."""
    from neti.insight.propose import MIN_BUCKETS, propose_budgets
    from neti.insight.report import build_report

    summary = build_report([_record("s", f"2026-08-0{n}", 50) for n in range(1, 4)])
    daily = next(p for p in propose_budgets(summary) if p.window == "day")

    assert not daily.actionable
    assert f"{MIN_BUCKETS} needed" in daily.rationale


def test_blocked_calls_are_not_counted_into_a_proposed_budget() -> None:
    """A budget is spent by calls that ran. A proposal derived otherwise proposes a ceiling too
    high to ever fire."""
    from neti.insight.report import build_report

    # The helper records `mode: observe`, where nothing is stopped and every call therefore counts.
    # Enforce is the mode where the distinction exists at all.
    ran = _record("s", "2026-08-01", 10, verdict="allow").model_copy(update={"mode": "enforce"})
    stopped = _record("s", "2026-08-01", 9_000, verdict="block").model_copy(
        update={"mode": "enforce"}
    )

    summary = build_report([ran, stopped])
    assert summary.bucket_totals[("day", "objects")] == {"2026-08-01": 10}


def test_in_observe_mode_a_would_be_block_still_counts() -> None:
    """Observe cannot stop anything, so the call ran and the budget is what it really came to.

    This is the traffic a first budget is meant to be derived from, and dropping the largest calls
    in it because of a verdict that was never enforced would propose a ceiling fitted to the
    quiet half of the week.
    """
    from neti.insight.report import build_report

    summary = build_report(
        [
            _record("s", "2026-08-01", 10, verdict="allow"),
            _record("s", "2026-08-01", 9_000, verdict="block"),
        ]
    )
    assert summary.bucket_totals[("day", "objects")] == {"2026-08-01": 9_010}


def test_the_budget_section_says_which_windows_it_did_not_propose() -> None:
    """A reader who sees `session` and `day` will assume the other two were rejected on evidence."""
    from neti.insight.propose import format_budget_proposals, propose_budgets
    from neti.insight.report import build_report

    days = [f"2026-08-{n:02d}" for n in range(1, 7)]
    summary = build_report([_record(f"s-{d}", d, 5) for d in days for _ in range(10)])
    text = format_budget_proposals(propose_budgets(summary))

    assert "window: day" in text, "the fragment is pasteable"
    assert "rolling:<n>h" in text and "week" in text, "the absent windows are named"


def test_nothing_recorded_proposes_no_budget_heading() -> None:
    """A fresh install must not see a dangling section with nothing under it."""
    from neti.insight.propose import format_budget_proposals, propose_budgets
    from neti.insight.report import build_report

    assert format_budget_proposals(propose_budgets(build_report([]))) == ""
