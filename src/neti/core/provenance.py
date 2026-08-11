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

    Separate from `untrusted` because sometimes the tool is the answer: every result of
    `read_inbox` is a stranger's words regardless of which mailbox it read.

    **Glob-matched, not compared for equality.** A name with no wildcard in it behaves exactly as it
    always did, and `mcp__scraper__*` now declares a whole MCP server untrusted in one line. Listing
    a federated server's tools by hand meant re-listing them whenever the server added one, and a
    tool nobody remembered to add was a tool whose output was silently trusted."""

    @property
    def declared(self) -> bool:
        return bool(self.untrusted or self.tools)


def taints(provenance: Provenance, tool: str, targets: tuple[str | None, ...]) -> Taint | None:
    """Does making this call put the session downstream of untrusted input?

    Asked *after* the call is decided, about the session's future — never about this call's own
    verdict. A read of an untrusted file is an ordinary read; what changes is what comes next.

    `targets` carries the call's gated targets **and its plain string arguments**, in that order.
    Gated targets alone were not enough: the argument that names untrusted input is very often one
    no resolver can size. `fetch(url="https://forum.example/thread/9")` has no cardinality, so it
    was never gated, so it never reached this function — and `untrusted: ["https://forum.example/**"]`
    matched nothing while reading as configured. Declaring the whole tool untrusted was the only
    option, which is far blunter than most operators want.

    Gated targets come first so that the recorded evidence names the resolved path where there is
    one, rather than whatever spelling the model happened to use.
    """
    if not provenance.declared:
        return None
    if matches(tool, tuple(provenance.tools)) is not None:
        first = next((t for t in targets if t), "")
        return Taint(pattern=f"tool:{tool}", target=first, tool=tool)
    for target in targets:
        if not target:
            continue
        hit = matches(target, provenance.untrusted)
        if hit is not None:
            return Taint(pattern=hit, target=target, tool=tool)
    return None
