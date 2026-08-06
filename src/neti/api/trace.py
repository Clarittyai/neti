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


# What each resolver actually needs to do its job. Read from the resolver name because that is the
# only thing the trace reliably knows, and stated as "nothing" where nothing is needed — a gate
# measuring local files holds no credential, and saying so is the more interesting claim.
_CREDENTIALS = {
    "entra.": "GroupMember.Read.All · read-only",
    "github.": "contents:read · read-only",
    "storage.": "s3:ListBucket · read-only",
    "db.": "select only, on the caller's connection",
}


def _credential(resolver: str) -> str:
    for prefix, scope in _CREDENTIALS.items():
        if resolver.startswith(prefix):
            return scope
    return "no credential — reads this machine"


def _preconditions(resolver: str) -> str:
    """The trap this resolver has to avoid, named."""
    if resolver.startswith("entra."):
        # Graph silently ignores `?$count=true` without this header — the fail-open that
        # RESOLVER_CONTRACT rule 4 exists to catch.
        return "ConsistencyLevel: eventual ✓ · expect text/plain ✓"
    if resolver in {"fs.paths", "shell.paths"}:
        return "walk cap set ✓ · a truncated walk is a floor, never a total ✓"
    if resolver.startswith("db."):
        return "statement recognised ✓ · counted, not estimated ✓"
    return "resolver contract ✓"


def _local(resolver: str, evidence: dict[str, Any]) -> str:
    """Where the number came from, for a resolver that opened no socket."""
    if resolver == "shell.paths":
        return f"parsed · {evidence.get('form') or evidence.get('reason') or 'command'}"
    if resolver == "fs.paths":
        return "walked this filesystem"
    return "resolved locally"


def _render(stage: str, p: dict[str, Any]) -> tuple[str, str, str] | None:
    """`(key, label, detail)` for a stage, or `None` to hide it.

    The detail line is mono type on screen and is deliberately the *wire*: the endpoint, the header,
    the status. Showing that we know Graph silently ignores `$count` without `ConsistencyLevel` is
    worth more to a technical audience than any animation.

    **Which wire, though.** Three of these lines were Graph's unconditionally — a scope, a
    `ConsistencyLevel` header and a `GET → 200` — so a `shell.paths` call that read the local
    filesystem was narrated as an authorised HTTP request to Microsoft. On the page whose own lede
    says *nothing here is pre-recorded*, about a decision that was entirely real. That is the worst
    version of this bug: not a stale label, but invented provenance presented as evidence.

    So the detail follows the resolver. A resolver that made no request does not get an HTTP line,
    and the ones that did keep theirs.
    """
    match stage:
        case s if s == INTERCEPTED:
            gated = "gated" if p.get("gated") else "not gated — out of scope"
            n = len(p.get("args") or {})
            return ("intercept", "Intercept", f"{p['tool']} · {gated} · {n} argument(s)")

        case s if s == BOUND:
            if not p.get("registered"):
                return ("bind", "Bind resolver", f"{p['resolver']} — NOT REGISTERED, fails closed")
            return ("bind", "Bind resolver", f"{p['resolver']} · {_credential(p['resolver'])}")

        case s if s == RESOLVE_STARTED:
            return ("assert", "Assert preconditions", _preconditions(str(p.get("resolver", ""))))

        case s if s == RESOLVED:
            ev = p.get("evidence") or {}
            url = str(ev.get("url", ""))
            # Only a resolver that actually made a request gets an HTTP line. `fs.paths` walks a
            # directory and `shell.paths` reads a string; narrating either as `GET → 200` was
            # describing a request that was never sent.
            where = (
                f"GET {url.split('graph.microsoft.com/v1.0', 1)[-1]} → {ev.get('status', 200)}"
                if url
                else _local(str(p.get("resolver", "")), ev)
            )
            if p.get("magnitude") is None:
                return ("count", "Count", f"{where} · {ev.get('reason', 'unresolved')}")
            return (
                "count",
                "Count",
                f"{where} · {p['magnitude']:,} {p['unit']} · {p['direction']}",
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
