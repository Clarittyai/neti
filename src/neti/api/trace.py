"""Turning engine stages into something a console can render.

The collector timestamps its own arrivals rather than trusting the engine to report timings. Two
reasons, and the second is the important one:

1. The engine then reads no clock it did not already read, so watching costs nothing when nobody is.
2. The number on screen is measured at the boundary the console actually observes. A separately
   derived "engine says 38.4ms" figure would be a second source of truth about the same event, and
   the first time the two disagreed nobody would know which to believe.

Stages are grouped into the seven the UI shows. That grouping lives here and not in `engine.py`,
because it is a presentation decision — the engine emits what it does, and how many boxes that
becomes is the console's business.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from neti.engine import BOUND, COMPARED, INTERCEPTED, RESOLVE_STARTED, RESOLVED, SEALED

__all__ = ["Stage", "TraceCollector"]


@dataclass
class Stage:
    """One visible step, with the wire detail that makes it credible."""

    key: str
    label: str
    detail: str
    at_ms: float
    """Milliseconds from the start of the call, measured at this collector."""

    took_ms: float
    payload: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "detail": self.detail,
            "at_ms": round(self.at_ms, 3),
            "took_ms": round(self.took_ms, 3),
            "payload": self.payload,
        }


class TraceCollector:
    """Accumulates stages for one `Engine.gate` call. Not reusable across calls."""

    def __init__(self) -> None:
        self._started = time.perf_counter()
        self._last = self._started
        self.stages: list[Stage] = []

    def __call__(self, stage: str, payload: dict[str, Any]) -> None:
        now = time.perf_counter()
        at_ms = (now - self._started) * 1000
        took_ms = (now - self._last) * 1000
        self._last = now

        rendered = _render(stage, payload)
        if rendered is None:
            return
        key, label, detail = rendered
        self.stages.append(
            Stage(
                key=key, label=label, detail=detail, at_ms=at_ms, took_ms=took_ms, payload=payload
            )
        )

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000

    def as_json(self) -> dict[str, Any]:
        return {
            "stages": [s.as_json() for s in self.stages],
            "elapsed_ms": round(self.elapsed_ms, 3),
        }


def _render(stage: str, p: dict[str, Any]) -> tuple[str, str, str] | None:
    """`(key, label, detail)` for a stage, or `None` to hide it.

    The detail line is mono type on screen and is deliberately the *wire*: the endpoint, the header,
    the status. Showing that we know Graph silently ignores `$count` without `ConsistencyLevel` is
    worth more to a technical audience than any animation.
    """
    match stage:
        case s if s == INTERCEPTED:
            gated = "gated" if p.get("gated") else "not gated — out of scope"
            n = len(p.get("args") or {})
            return ("intercept", "Intercept", f"{p['tool']} · {gated} · {n} argument(s)")

        case s if s == BOUND:
            if not p.get("registered"):
                return ("bind", "Bind resolver", f"{p['resolver']} — NOT REGISTERED, fails closed")
            return (
                "bind",
                "Bind resolver",
                f"{p['resolver']} · GroupMember.Read.All · read-only",
            )

        case s if s == RESOLVE_STARTED:
            # The precondition beat. Graph silently ignores `?$count=true` without this header,
            # which is the fail-open RESOLVER_CONTRACT rule 4 exists to catch.
            return (
                "assert",
                "Assert preconditions",
                "ConsistencyLevel: eventual ✓ · expect text/plain ✓",
            )

        case s if s == RESOLVED:
            ev = p.get("evidence") or {}
            url = str(ev.get("url", ""))
            path = url.split("graph.microsoft.com/v1.0", 1)[-1] if url else "—"
            if p.get("magnitude") is None:
                reason = ev.get("reason", "unresolved")
                return ("count", "Count", f"GET {path} → {ev.get('status', '—')} · {reason}")
            return (
                "count",
                "Count",
                f"GET {path} → {ev.get('status', 200)} "
                f"· {p['magnitude']:,} {p['unit']} · {p['direction']}",
            )

        case s if s == COMPARED:
            rows = []
            for a in p.get("args") or []:
                for b in a.get("breaches") or []:
                    rows.append(f"{b['observed']:,} > {b['above']:,} → {b['verdict']}")
            rows.append(f"join → {str(p['verdict']).upper()}")
            return ("compare", "Compare", " · ".join(rows))

        case s if s == SEALED:
            prev = (p.get("prev_digest") or "genesis")[:8]
            return ("seal", "Seal", f"blake2b · prev {prev}… → {p['record_digest'][:8]}…")

    return None
