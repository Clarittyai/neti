"""`--since`: report and propose over a window rather than over all history.

The README has promised `neti report --since 7d` since the first draft and the flag did not exist —
running the documented command printed a usage error. This is that flag.

It matters beyond the documentation being true. `neti propose` reads a distribution and suggests
ceilings from it, and a distribution assembled from *all* history is the wrong input twice over: it
includes traffic from before the last policy change, and it dilutes a recent shift in behaviour
under months of older calls. A ceiling proposed from a stale window is a ceiling nobody should
commit.

Filtering happens on `decided_at`, which is the caller-supplied timestamp already in every record —
so nothing here reads a clock that the decision path did not. The *cutoff* reads one, because a
window is a question about now, and that is a property of the report rather than of the decision.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta

from neti.core.record import DecisionRecord

__all__ = ["WindowError", "parse_since", "within"]

_PATTERN = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.I)
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


class WindowError(ValueError):
    """An unparseable window. Never silently treated as "no window"."""


def parse_since(since: str) -> timedelta:
    """`7d` -> 7 days. Also `30m`, `12h`, `2w`, `90s`.

    A bad value raises rather than defaulting to all-history. Silently widening the window would
    make `--since notaduration` look like it worked and quietly propose ceilings from the wrong
    distribution, which is the failure this whole module exists to prevent.
    """
    match = _PATTERN.match(since)
    if not match:
        raise WindowError(
            f"cannot read {since!r} as a window. Use a number and a unit: 90s, 30m, 12h, 7d, 2w."
        )
    amount, unit = int(match.group(1)), match.group(2).lower()
    if amount == 0:
        raise WindowError("a window of zero selects nothing — omit --since to use all history")
    return timedelta(**{_UNITS[unit]: amount})


def within(
    records: Iterable[DecisionRecord], since: timedelta, *, now: datetime | None = None
) -> Iterator[DecisionRecord]:
    """The records decided inside the window.

    A record whose `decided_at` cannot be parsed is **kept**, not dropped. These files are a hash
    chain and the honest default for an unreadable field is to include the record and let the
    operator see it, rather than to quietly shrink the corpus a proposal is computed from.
    """
    cutoff = (now or datetime.now(UTC)) - since
    for record in records:
        try:
            decided = datetime.fromisoformat(record.decided_at)
        except (TypeError, ValueError):
            yield record
            continue
        if decided.tzinfo is None:
            # Written by something that dropped the offset. Assume UTC rather than local time: the
            # records are UTC by construction, and guessing local would shift the window by hours.
            decided = decided.replace(tzinfo=UTC)
        if decided >= cutoff:
            yield record
