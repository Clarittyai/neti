"""`--since`, and the ways a window can quietly lie.

The README has shown `neti report --since 7d` since the first draft, and the flag did not exist —
the documented command printed a usage error. That is the shallow reason this module exists.

The real one is `neti propose`. It reads a distribution and suggests ceilings from it, so the window
*is* the input: all-history includes traffic from before the last policy change and buries a recent
shift under months of older calls. Every test here is about a way the window could silently be the
wrong one, because a wrong window does not fail — it produces a plausible number that nobody should
commit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from neti.core.record import DecisionRecord
from neti.insight.window import WindowError, parse_since, within

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def record(decided_at: str, tool: str = "send_email") -> DecisionRecord:
    return DecisionRecord(
        decision_id=f"d-{decided_at}",
        decided_at=decided_at,
        tool=tool,
        args={},
        verdict="allow",
        rule="",
        mode="observe",
        causes=(),
        policy_digest="p",
        code_version="0.1.0",
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7d", timedelta(days=7)),
        ("90s", timedelta(seconds=90)),
        ("30m", timedelta(minutes=30)),
        ("12h", timedelta(hours=12)),
        ("2w", timedelta(weeks=2)),
        ("  7d ", timedelta(days=7)),
        ("7D", timedelta(days=7)),
    ],
)
def test_windows_people_actually_type(text: str, expected: timedelta) -> None:
    assert parse_since(text) == expected


@pytest.mark.parametrize("bad", ["7", "d", "7 days", "last week", "-7d", "7y", "", "0d"])
def test_an_unreadable_window_is_refused_rather_than_widened(bad: str) -> None:
    """The important one.

    Defaulting a bad `--since` to all-history would make `--since lastweek` look like it worked and
    quietly propose ceilings from the wrong distribution. Nothing about the output would say so.
    """
    with pytest.raises(WindowError):
        parse_since(bad)


def test_it_selects_by_decided_at() -> None:
    rows = [
        record("2026-07-31T11:00:00+00:00"),  # 1h ago
        record("2026-07-30T12:00:00+00:00"),  # 1d ago
        record("2026-07-01T12:00:00+00:00"),  # 30d ago
    ]
    kept = list(within(rows, timedelta(days=7), now=NOW))
    assert len(kept) == 2
    assert all("2026-07-3" in r.decided_at for r in kept)


def test_the_boundary_is_inclusive() -> None:
    """A record exactly on the cutoff is in the window. Off-by-one here silently drops the oldest
    day of a `7d` proposal, which is the day most likely to hold the outlier."""
    exactly = (NOW - timedelta(days=7)).isoformat()
    assert list(within([record(exactly)], timedelta(days=7), now=NOW))


def test_a_naive_timestamp_is_read_as_utc_not_local() -> None:
    """Records are UTC by construction. Guessing local time would shift the window by hours and, in
    the worst case, silently exclude a whole evening's traffic."""
    naive = "2026-07-31T11:30:00"  # 30 minutes before NOW, if UTC
    assert list(within([record(naive)], timedelta(hours=1), now=NOW))


def test_an_unparseable_timestamp_is_kept_not_dropped() -> None:
    """These files are a hash chain. The honest default for a field we cannot read is to include
    the record and let the operator see it, not to quietly shrink the corpus a ceiling comes from.
    """
    kept = list(within([record("not-a-timestamp")], timedelta(days=1), now=NOW))
    assert len(kept) == 1


def test_no_window_means_everything() -> None:
    """`--since` is optional, and its absence must not narrow anything."""
    rows = [record("2020-01-01T00:00:00+00:00"), record("2026-07-31T11:00:00+00:00")]
    assert len(list(within(rows, timedelta(days=36500), now=NOW))) == 2
