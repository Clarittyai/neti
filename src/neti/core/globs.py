"""Glob matching over targets, with `**` spanning separators and `*` not.

Lifted out of `core/provenance.py` when `sensitive:` needed the same vocabulary. This repository has
been bitten more than once by a primitive copied rather than shared and then drifting — the sidebar
row against clarity-platform being the most recent — and two glob engines that disagree about what
`*` means would be a far more expensive version of that, because they would disagree about which
calls are gated.

**`fnmatch` is not usable here.** Its `*` matches `/` too, so `secrets/*` would cover
`secrets/a/b/c` and a rule meant to name one directory would silently name a tree. The distinction
between `*` and `**` is the entire vocabulary of these patterns, and a rule that quietly widens is
the one failure mode a security control cannot have.
"""

from __future__ import annotations

import posixpath
import re

__all__ = ["matches"]


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
