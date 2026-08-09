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
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from neti.core.budget import SessionTally
from neti.core.types import ArgDecision
from neti.store.jsonl import _exclusive

__all__ = ["SessionStore"]

_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_AGE_S = 7 * 24 * 60 * 60
"""Sessions older than a week are swept on write. A session id is a conversation, and nobody's
conversation spans a week — what accumulates otherwise is a directory of dead files."""


def _safe_name(session_id: str) -> str:
    """A session id is agent-supplied, so it is a filename only after this.

    `../../etc/passwd` is a legal string and an illegal filename, and this writes to a path derived
    from it. Everything outside a small alphabet becomes an underscore, and the result is truncated
    — the id is a key here, not something anybody reads back.
    """
    return _SAFE.sub("_", session_id)[:120] or "anonymous"


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


class SessionStore:
    """Per-session running totals, on disk beside the records.

    Every method degrades rather than raises. A gate that stopped working because a cache file was
    unreadable would be trading a real guarantee for a bookkeeping one — the same rule
    `gatekeeper.py` applies to the record itself: the decision survives its own filing.
    """

    def __init__(self, records: str | Path) -> None:
        self.root = Path(records).parent / "sessions"

    def _path(self, session_id: str) -> Path:
        return self.root / f"{_safe_name(session_id)}.json"

    def load(self, session_id: str) -> SessionTally:
        try:
            return _parse(self._path(session_id).read_text(encoding="utf-8"))
        except OSError:
            # No file, or one we cannot read. An empty tally under-counts, which is the direction
            # that costs a missed budget rather than a wrongly blocked call.
            return SessionTally()

    def add(self, session_id: str, args: tuple[ArgDecision, ...]) -> SessionTally:
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
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(session_id)
            with _THREADS, path.open("a+", encoding="utf-8") as fh, _exclusive(fh):
                fh.seek(0)
                current = _parse(fh.read())
                updated = current.add_committed(args)
                fh.seek(0)
                fh.truncate()
                json.dump({"totals": dict(updated.totals), "calls": updated.calls}, fh)
                return updated
        except (OSError, ValueError, TypeError):
            # Same contract as `load`: a bookkeeping failure never becomes a gate failure. The
            # caller gets a tally that is behind rather than an exception it has no answer for.
            return self.load(session_id).add_committed(args)

    def sweep(self) -> None:
        """Drop sessions nobody will add to again.

        Best effort, and never on the path that decides anything."""
        try:
            cutoff = time.time() - _MAX_AGE_S
            for entry in os.scandir(self.root):
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    Path(entry.path).unlink(missing_ok=True)
        except OSError:
            return
