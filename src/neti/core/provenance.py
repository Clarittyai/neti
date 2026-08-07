"""Downstream of untrusted input: a fact about a session, not a judgement about a call.

Every defence in this product so far answers *how big*. That is blind to the shape of a prompt
injection, which is not big at all until the last step:

    read_files("customer_data/ticket_*.md")   ← a ticket a stranger wrote
          ↓ same session
    purge("customer_data")                    ← what the ticket told it to do

The gate stopped the second call in the demo because 2,240 exceeded a ceiling. It would not have
stopped `purge("src/secrets.env")` — one object, under every ceiling — and that is the same attack
with a better-chosen target.

**What this adds, and what it deliberately does not.** It does not read the agent's reasoning, ask
what it meant, or score anything. It answers one mechanical question:

    has this session already touched a target the operator declared untrusted?

That is checkable, replayable, and — critically — **not something an attacker can write**. The
injected text can say anything it likes about being authorised; it cannot change the fact that the
session read from `customer_data/` two calls ago.

**Escalate only.** Being downstream of untrusted input applies a *second, tighter* set of bands on
top of the declared ones. It can raise a verdict and never lower one, so a mistake here costs a
confirmation, never a silent allow. That is the same asymmetry the whole product is built on.

**A call cannot taint itself.** The read that ingests untrusted content is judged under the ordinary
ceilings; the tightening applies from the *next* call onward. Anything else would make the first
read of any untrusted file impossible, which is the entire job of a support agent.
"""

from __future__ import annotations

from dataclasses import dataclass

from neti.core.globs import matches

__all__ = ["Provenance", "matches", "taints"]


@dataclass(frozen=True)
class Taint:
    """Why a session is downstream of untrusted input. Recorded, so replay can take it as given."""

    pattern: str
    target: str
    tool: str

    def as_json(self) -> dict[str, str]:
        return {"pattern": self.pattern, "target": self.target, "tool": self.tool}


@dataclass(frozen=True)
class Provenance:
    """What the operator declared about where untrusted content lives."""

    untrusted: tuple[str, ...] = ()
    tools: frozenset[str] = frozenset()
    """Tools whose *results* are untrusted whatever their argument — a web fetch, an inbox read.

    Separate from `untrusted` because the target of `fetch_url("https://…")` is not a path and no
    glob over the filesystem will ever name it."""

    @property
    def declared(self) -> bool:
        return bool(self.untrusted or self.tools)


def taints(provenance: Provenance, tool: str, targets: tuple[str | None, ...]) -> Taint | None:
    """Does making this call put the session downstream of untrusted input?

    Asked *after* the call is decided, about the session's future — never about this call's own
    verdict. A read of an untrusted file is an ordinary read; what changes is what comes next.
    """
    if not provenance.declared:
        return None
    if tool in provenance.tools:
        first = next((t for t in targets if t), "")
        return Taint(pattern=f"tool:{tool}", target=first, tool=tool)
    for target in targets:
        if not target:
            continue
        hit = matches(target, provenance.untrusted)
        if hit is not None:
            return Taint(pattern=hit, target=target, tool=tool)
    return None
