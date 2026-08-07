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

import posixpath
import re
from dataclasses import dataclass

__all__ = ["Provenance", "matches", "taints"]


def _to_regex(pattern: str) -> re.Pattern[str]:
    """Glob → regex, with `**` spanning separators and `*` not.

    `fnmatch` is not usable here: its `*` matches `/` too, so `customer_data/*` would match
    `customer_data/a/b/c` and a rule meant to name one directory would silently name a tree. The
    distinction between `*` and `**` is the whole vocabulary of these patterns.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif char == "*":
            out.append("[^/]*")
            i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(char))
            i += 1
    return re.compile(f"^{''.join(out)}$")


def _normalise(target: str) -> str:
    """`./a//b` -> `a/b`, and Windows separators folded, so a pattern written once matches.

    Deliberately textual. Resolving against the filesystem would make this depend on what exists,
    and a rule about provenance has to hold for a path that is about to be created as much as for
    one that is there.
    """
    text = target.replace("\\", "/").strip()
    text = posixpath.normpath(text) if text else text
    return text[2:] if text.startswith("./") else text


def matches(target: str, patterns: tuple[str, ...]) -> str | None:
    """The first pattern this target matches, or `None`.

    The pattern is returned rather than a bool because the record has to say *which* rule fired.
    "This session is tainted" is an assertion; "this session read `customer_data/**` at 14:02" is
    evidence.
    """
    if not target:
        return None
    text = _normalise(target)
    for pattern in patterns:
        clean = _normalise(pattern)
        if _to_regex(clean).match(text):
            return pattern
        # A pattern naming a directory taints everything under it, so `customer_data` covers
        # `customer_data/ticket_1.md` without the operator having to write `/**`.
        #
        # A prefix test, **not** a match against the target's parent directory. That was the first
        # version and it was wrong in the direction that matters: `dirname("customer_data/a/b.md")`
        # is `customer_data/a`, which `customer_data/*` matches — so a rule naming one level
        # silently named the whole tree, and `*` stopped meaning anything different from `**`.
        if "*" not in clean and "?" not in clean and text.startswith(f"{clean.rstrip('/')}/"):
            return pattern
    return None


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
