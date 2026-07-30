"""RFC 6901 JSON pointers into a tool call's argument tree.

Gated arguments are addressed by pointer rather than by name so that nested and array targets are
addressable: `/to/0`, `/members`, `/filter/group`. A policy that could only name top-level string
params would be unable to gate the shapes real MCP tools actually use.

Extension: a `#suffix` on a pointer names a second *unit* resolved from the same target — `/group`
resolves principals, `/group#apps` resolves application assignments from the same group id. The
suffix is stripped before traversal and preserved in the decision record.
"""

from __future__ import annotations

from typing import Any

__all__ = ["PointerError", "escape_token", "resolve_pointer", "split_unit_suffix"]


class PointerError(LookupError):
    """The pointer does not address anything in this argument tree."""


def split_unit_suffix(pointer: str) -> tuple[str, str | None]:
    """`"/group#apps"` -> `("/group", "apps")`; `"/to"` -> `("/to", None)`."""
    base, sep, suffix = pointer.partition("#")
    return (base, suffix) if sep else (pointer, None)


def _unescape_token(token: str) -> str:
    # ~1 before ~0, per RFC 6901 s4 — the other order corrupts a literal "~1".
    return token.replace("~1", "/").replace("~0", "~")


def escape_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """Return the value a pointer addresses, or raise `PointerError`.

    Absent is distinct from present-and-None: an absent gated argument means the agent did not
    supply the target, which the caller must decide about explicitly rather than treating as empty.
    """
    base, _ = split_unit_suffix(pointer)
    if base == "":
        return doc
    if not base.startswith("/"):
        raise PointerError(f"pointer must be empty or start with '/': {pointer!r}")

    current = doc
    for raw in base.split("/")[1:]:
        token = _unescape_token(raw)
        if isinstance(current, dict):
            if token not in current:
                raise PointerError(f"{pointer!r}: no key {token!r}")
            current = current[token]
        elif isinstance(current, list | tuple):
            if token == "-" or not token.isdigit():
                raise PointerError(f"{pointer!r}: {token!r} is not an array index")
            idx = int(token)
            if idx >= len(current):
                raise PointerError(f"{pointer!r}: index {idx} out of range")
            current = current[idx]
        else:
            raise PointerError(f"{pointer!r}: cannot descend into {type(current).__name__}")
    return current
