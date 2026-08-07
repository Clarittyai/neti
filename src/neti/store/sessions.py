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
import time
from pathlib import Path

from neti.core.budget import SessionTally
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
            raw = json.loads(self._path(session_id).read_text(encoding="utf-8"))
            totals = {str(k): int(v) for k, v in (raw.get("totals") or {}).items()}
            return SessionTally(totals=totals, calls=int(raw.get("calls") or 0))
        except (OSError, ValueError, TypeError):
            # No file, unreadable file, or a half-written one. An empty tally under-counts, which
            # is the direction that costs a missed budget rather than a wrongly blocked call.
            return SessionTally()

    def save(self, session_id: str, tally: SessionTally) -> None:
        """Write the tally, serialised against other hook processes in the same session.

        Locked because parallel tool calls are real — a harness may run several at once, and two
        unsynchronised read-modify-writes lose one of the increments. Under-counting is the safe
        direction, but it is not free, and the lock is microseconds.
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(session_id)
            with path.open("a+", encoding="utf-8") as fh, _exclusive(fh):
                fh.seek(0)
                fh.truncate()
                json.dump({"totals": dict(tally.totals), "calls": tally.calls}, fh)
        except OSError:
            return

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
