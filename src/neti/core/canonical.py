"""Canonical JSON serialisation, RFC 8785 (JCS) with a no-float override.

Two jobs. It makes the record hashable in a way another implementation can reproduce, and it makes
"the same decision" a byte-level claim rather than a feeling.

The override matters: RFC 8785 canonicalises numbers via the ES6 double rules, which is the classic
place these schemes break across languages. No field that enters the hash may be a float. Magnitudes
and thresholds are integers by construction, so there is nothing to lose by banning them outright.
"""

from __future__ import annotations

import unicodedata
from typing import Any

__all__ = ["CanonicalError", "canonical_bytes", "canonical_json"]


class CanonicalError(TypeError):
    """A value cannot be canonicalised, so it must not enter the hash chain."""


def _fail(value: Any) -> str:
    raise CanonicalError(
        f"{type(value).__name__} is not canonicalisable: {value!r}. "
        "Floats, NaN, Infinity and arbitrary objects are banned from chained fields."
    )


def _encode(value: Any) -> str:
    match value:
        case None:
            return "null"
        case bool():
            return "true" if value else "false"
        case int():
            return str(value)
        case str():
            return _encode_string(value)
        case dict():
            items = []
            for key in sorted(value, key=_sort_key):
                if not isinstance(key, str):
                    raise CanonicalError(f"object keys must be strings, got {type(key).__name__}")
                items.append(f"{_encode_string(key)}:{_encode(value[key])}")
            return "{" + ",".join(items) + "}"
        case list() | tuple():
            return "[" + ",".join(_encode(v) for v in value) + "]"
        case _:
            return _fail(value)


def _sort_key(key: str) -> tuple[int, ...]:
    """RFC 8785 sorts object keys by UTF-16 code unit, not by code point."""
    return tuple(key.encode("utf-16-be"))


_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _encode_string(value: str) -> str:
    out = ['"']
    for ch in value:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ch < "\x20":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def canonical_json(value: Any) -> str:
    """Canonical form. Strings are NFC-normalised first, per RFC 8785 s3.2.3."""
    return _encode(_normalise(value))


def canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def _normalise(value: Any) -> Any:
    match value:
        case str():
            return unicodedata.normalize("NFC", value)
        case dict():
            return {_normalise(k): _normalise(v) for k, v in value.items()}
        case list() | tuple():
            return [_normalise(v) for v in value]
        case _:
            return value
