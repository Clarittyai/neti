"""Session totals that survive between processes.

`SessionTally` lives in memory on the `Engine`, which is correct for the MCP gateway — one
long-running process, one engine, one session — and **structurally inert for the integration most
people use.** `neti hook` is invoked once per tool call, so every call built a fresh engine with an
empty tally, and a declared session budget never fired. Demonstrated: a budget of 3 objects, six
single-object reads through the real hook, all allowed.

That made a `SCOPE.md` claim false exactly where it mattered:

> **NC-01** Cumulative effect across calls … *Mitigated only by declared session budgets.*

For a Claude Code user it was not mitigated at all, because the mitigation could not run. And
cumulative effect is most of what a coding agent's traffic *is* — measured over a simulated week,
178 of 320 calls were single-file operations of magnitude 1, which no per-call ceiling can ever see.

**Why a sidecar rather than the record chain.** The records already hold every magnitude and every
`session_id`, so the tally could be recomputed from them — and must not be. `neti hook` was
deliberately changed to stop reading that file per call: it used to, and the cost grew with
everything already recorded (133ms fresh, 273ms at ten thousand records, 816ms at fifty thousand).
Reintroducing an O(n) read to count would undo that on the hot path of every tool call in a session.

So: one small file per session, read and written in constant time, holding nothing that the chain
does not already hold authoritatively. It is a cache for a decision input, not evidence — if it is
lost, the budget under-counts and the chain is untouched.

**Windows wider than a session.** `glean-bulk-download` in the incident corpus is 8,000,000 objects
accumulated across many retrievals, and a per-session budget cannot see it: each new conversation
starts at zero, so an agent that reads steadily for three days never trips one. `window: day`,
`window: week` and `window: rolling:<n>h` key the same sidecar by a *time* bucket instead of by the
conversation, so the total spans every session on this machine.

The clock is read here and nowhere near the decision. `neti.core` may not read one at all
(`tests/property/test_core_is_pure.py`), so a `Window` is parsed in `core.budget` and resolved to a
bucket here, and the resolved total is passed in as an ordinary argument. A recorded decision
therefore still replays to the same verdict from its own evidence.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from neti.core.budget import SessionTally, Window, WindowKind
from neti.core.provenance import Taint
from neti.core.types import ArgDecision
from neti.store.jsonl import _exclusive

__all__ = ["MemoryTallies", "SessionStore", "bucket_key"]

_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_AGE_S = 7 * 24 * 60 * 60
"""Buckets older than a week are swept on write. A session id is a conversation, and nobody's
conversation spans a week; a day or week bucket is finished the moment its window is. What
accumulates otherwise is a directory of dead files.

A `rolling:` bucket is rewritten on every call, so its mtime never goes stale and the sweep never
takes it — correctly, since the file is live for as long as the policy declaring it is."""


def _safe_name(session_id: str) -> str:
    """A session id is agent-supplied, so it is a filename only after this.

    `../../etc/passwd` is a legal string and an illegal filename, and this writes to a path derived
    from it. Everything outside a small alphabet becomes an underscore, and the result is truncated
    — the id is a key here, not something anybody reads back.
    """
    return _SAFE.sub("_", session_id)[:120] or "anonymous"


def bucket_key(window: Window, session_id: str, now: float) -> str:
    """Which sidecar holds the running total for this window, right now.

    A `session` window keeps the filename it always had, so an install that upgrades into windows
    does not silently reset the totals it was already keeping. Every other window is keyed by time
    alone and is therefore shared across sessions — which is the entire reason to declare one.

    Time buckets are UTC. Local time would move the boundary when the machine moves, and a budget
    that resets an hour early because somebody flew to Berlin is a budget nobody can reason about.
    """
    if window.kind is WindowKind.SESSION:
        return _safe_name(session_id)
    stamp = datetime.fromtimestamp(now, tz=UTC)
    if window.kind is WindowKind.DAY:
        return f"day-{stamp:%Y-%m-%d}"
    if window.kind is WindowKind.WEEK:
        return f"week-{stamp:%G-W%V}"
    return f"rolling-{window.hours}h"


def _hour_of(now: float) -> int:
    return int(now // 3600)


_THREADS = threading.Lock()
"""Serialises threads within this process; `_exclusive` serialises processes. Held for the length
of one small read-modify-write, so contention is not worth measuring."""


def _parse(raw: str) -> SessionTally:
    """A stored tally, or an empty one. Never raises — a half-written file is not a reason to
    fail a tool call."""
    try:
        data = json.loads(raw or "{}")
        totals = {str(k): int(v) for k, v in (data.get("totals") or {}).items()}
        return SessionTally(totals=totals, calls=int(data.get("calls") or 0))
    except (ValueError, TypeError, AttributeError):
        return SessionTally()


def _parse_taint(raw: str) -> Taint | None:
    """The session's recorded taint, if it has one. Never raises, like everything else here."""
    try:
        data = json.loads(raw or "{}").get("taint")
        if not isinstance(data, dict):
            return None
        return Taint(
            pattern=str(data["pattern"]), target=str(data["target"]), tool=str(data["tool"])
        )
    except (ValueError, TypeError, AttributeError, KeyError):
        return None


def _parse_hours(raw: str) -> dict[int, SessionTally]:
    """A rolling bucket's per-hour sub-totals.

    A rolling window cannot be one running number: to know the last 24 hours you have to be able to
    *drop* the twenty-fifth, and a single total can only ever grow. So the file keeps one sub-total
    per hour and the read sums the ones still in range. The cost is bounded by
    `MAX_ROLLING_HOURS` — a fixed 168 integers at the very worst, and independent of how much the
    agent has done, which is the property `sessions.py` exists to protect.
    """
    try:
        data = json.loads(raw or "{}")
        return {
            int(hour): _parse(json.dumps(entry))
            for hour, entry in (data.get("hours") or {}).items()
        }
    except (ValueError, TypeError, AttributeError):
        return {}


def _within(hours: dict[int, SessionTally], now: float, span: int) -> dict[int, SessionTally]:
    """The sub-totals still inside a rolling window, current hour included.

    A clock that jumps backwards — an NTP correction, a laptop waking in another timezone — would
    otherwise leave sub-totals stamped in the future counting forever. They are kept rather than
    discarded: over-counting costs a confirmation, and dropping them would hand anyone who can move
    the clock a way to zero the budget.
    """
    oldest = _hour_of(now) - span + 1
    return {hour: tally for hour, tally in hours.items() if hour >= oldest}


def _summed(hours: dict[int, SessionTally]) -> SessionTally:
    total = SessionTally()
    for _, tally in sorted(hours.items()):
        merged = dict(total.totals)
        for unit, value in tally.totals.items():
            merged[unit] = merged.get(unit, 0) + value
        total = SessionTally(totals=merged, calls=total.calls + tally.calls)
    return total


class SessionStore:
    """Per-session running totals, on disk beside the records.

    Every method degrades rather than raises. A gate that stopped working because a cache file was
    unreadable would be trading a real guarantee for a bookkeeping one — the same rule
    `gatekeeper.py` applies to the record itself: the decision survives its own filing.
    """

    def __init__(self, records: str | Path) -> None:
        self.root = Path(records).parent / "sessions"

    def _path(self, window: Window, session_id: str, now: float) -> Path:
        return self.root / f"{bucket_key(window, session_id, now)}.json"

    def load(self, window: Window, session_id: str, now: float) -> SessionTally:
        """The running total for this window, as of `now`."""
        try:
            raw = self._path(window, session_id, now).read_text(encoding="utf-8")
        except OSError:
            # No file, or one we cannot read. An empty tally under-counts, which is the direction
            # that costs a missed budget rather than a wrongly blocked call.
            return SessionTally()
        if window.kind is WindowKind.ROLLING:
            return _summed(_within(_parse_hours(raw), now, window.hours))
        return _parse(raw)

    def add(
        self,
        window: Window,
        session_id: str,
        now: float,
        args: tuple[ArgDecision, ...],
    ) -> SessionTally:
        """Apply this call's magnitudes to the stored total, atomically. Returns the new total.

        **Read and write under one lock, because locking only the write does not help.** The
        previous shape was `load()` … decide … `save()`, with the lock around `save` alone and a
        comment claiming that addressed the lost update. It does not: two processes both read 3,
        both write 4, and one increment is gone. Driven with 24 hook processes at once against a
        single session, **7 of 24 calls were counted** — a 71% loss on the one mechanism `SCOPE.md`
        names as the mitigation for cumulative effect (NC-01), in exactly the situation that makes
        cumulative effect worth watching.

        Parallel tool calls are not exotic. A harness that batches several in one turn is ordinary,
        and this repository's own agent does it constantly.

        The *verdict* for two simultaneous calls still races, and that is inherent — neither can see
        a total the other has not written yet. What must not happen is the total forgetting them
        afterwards.

        **Two locks, because one of them does not do what it looks like it does.** `flock` and
        `LockFileEx` are held by the *process*, so two threads in one process both acquire it and
        neither waits: with the file lock alone, 64 threads produced 23 increments. Processes are
        the deployment `neti hook` uses, threads are the one `neti gate` uses — uvicorn serves
        concurrently — so both are real and only the pair covers both. The subprocess test passed
        against the file lock alone, which is how this nearly shipped.

        Every window uses this same read-modify-write under the same pair of locks. A rolling
        window differs only in what is written: the current hour's sub-total is incremented and
        anything now out of range is dropped in the same pass, so the file is pruned by the traffic
        that keeps it alive rather than by a sweep that might never run.
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(window, session_id, now)
            with _THREADS, path.open("a+", encoding="utf-8") as fh, _exclusive(fh):
                fh.seek(0)
                raw = fh.read()
                payload: dict[str, object]
                if window.kind is WindowKind.ROLLING:
                    hours = _within(_parse_hours(raw), now, window.hours)
                    current = _hour_of(now)
                    hours[current] = hours.get(current, SessionTally()).add_committed(args)
                    payload = {
                        "hours": {
                            str(hour): {"totals": dict(t.totals), "calls": t.calls}
                            for hour, t in sorted(hours.items())
                        }
                    }
                    updated = _summed(hours)
                else:
                    updated = _parse(raw).add_committed(args)
                    payload = {"totals": dict(updated.totals), "calls": updated.calls}
                    # The session file holds the taint as well as the totals, and this rewrites the
                    # whole file. Without carrying it across, the first budgeted call after a taint
                    # would erase it — silently restoring exactly the defect this store exists to
                    # fix, in the one configuration where both features are switched on.
                    carried = _parse_taint(raw)
                    if carried is not None:
                        payload["taint"] = carried.as_json()
                fh.seek(0)
                fh.truncate()
                json.dump(payload, fh)
                return updated
        except (OSError, ValueError, TypeError):
            # Same contract as `load`: a bookkeeping failure never becomes a gate failure. The
            # caller gets a tally that is behind rather than an exception it has no answer for.
            return self.load(window, session_id, now).add_committed(args)

    def load_taint(self, session_id: str) -> Taint | None:
        """Has this session already read something the operator declared untrusted?

        Kept in the *session* bucket whatever windows the budgets use, because a taint is a fact
        about one conversation. A daily taint would mean an agent that read a support ticket at
        09:00 is still downstream of it at 17:00 in an unrelated conversation, which is not what
        provenance claims and would be an interrupt nobody could explain.
        """
        try:
            return _parse_taint(self._path(Window(), session_id, 0.0).read_text(encoding="utf-8"))
        except OSError:
            return None

    def remember_taint(self, session_id: str, taint: Taint) -> None:
        """Latch a taint onto the session. First one wins; there is no un-reading it.

        Best effort in the same direction as everything else here: if this cannot be written the
        session simply stays untainted, which under-protects rather than over-blocks. That is the
        wrong direction to fail in for a *security* control and the right one for a *cache*, and
        this is a cache — the authority is the record chain, which already carries the taint on
        every decision it applied to.
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(Window(), session_id, 0.0)
            with _THREADS, path.open("a+", encoding="utf-8") as fh, _exclusive(fh):
                fh.seek(0)
                raw = fh.read()
                if _parse_taint(raw) is not None:
                    return
                tally = _parse(raw)
                fh.seek(0)
                fh.truncate()
                json.dump(
                    {
                        "totals": dict(tally.totals),
                        "calls": tally.calls,
                        "taint": taint.as_json(),
                    },
                    fh,
                )
        except (OSError, ValueError, TypeError):
            return

    def sweep(self) -> None:
        """Drop buckets nobody will add to again.

        Best effort, and never on the path that decides anything."""
        try:
            cutoff = time.time() - _MAX_AGE_S
            for entry in os.scandir(self.root):
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    Path(entry.path).unlink(missing_ok=True)
        except OSError:
            return


class MemoryTallies:
    """The same interface as `SessionStore`, held in memory for one process.

    This exists so that **windows mean the same thing on both paths.** The engine used to keep a
    bare `dict[str, SessionTally]` and reach into it directly, which was fine while `session` was
    the only window: a dict keyed by session id *is* a session window. It stops being fine the
    moment a `day` window has to roll over at midnight and a `rolling:` window has to forget its
    twenty-fifth hour — behaviour that already exists in `SessionStore` and must not be written a
    second time, slightly differently, where nothing compares the two.

    So `neti gate` (one long-running process, nothing on disk) and `neti hook` (one process per
    call, a sidecar per bucket) now run the same window arithmetic, and the only difference between
    them is whether the total survives the process.
    """

    def __init__(self) -> None:
        self._flat: dict[str, SessionTally] = {}
        self._hours: dict[str, dict[int, SessionTally]] = {}
        self._taints: dict[str, Taint] = {}

    def load(self, window: Window, session_id: str, now: float) -> SessionTally:
        key = bucket_key(window, session_id, now)
        if window.kind is WindowKind.ROLLING:
            return _summed(_within(self._hours.get(key, {}), now, window.hours))
        return self._flat.get(key, SessionTally())

    def add(
        self,
        window: Window,
        session_id: str,
        now: float,
        args: tuple[ArgDecision, ...],
    ) -> SessionTally:
        key = bucket_key(window, session_id, now)
        if window.kind is WindowKind.ROLLING:
            hours = _within(self._hours.get(key, {}), now, window.hours)
            current = _hour_of(now)
            hours[current] = hours.get(current, SessionTally()).add_committed(args)
            self._hours[key] = hours
            return _summed(hours)
        updated = self._flat.get(key, SessionTally()).add_committed(args)
        self._flat[key] = updated
        return updated

    def load_taint(self, session_id: str) -> Taint | None:
        return self._taints.get(_safe_name(session_id))

    def remember_taint(self, session_id: str, taint: Taint) -> None:
        self._taints.setdefault(_safe_name(session_id), taint)

    def sweep(self) -> None:
        """Nothing to do: a process that ends takes its tallies with it."""
        return
