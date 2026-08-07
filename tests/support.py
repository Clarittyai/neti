"""Reading source in a test, without reading your own prose back.

Twice in one session a test here passed for the wrong reason, and both times the same way:

    # the sidebar row lost its `rounded-lg`
    assert "rounded-lg" in shell            # ← satisfied by the COMMENT explaining the rule

    # the guard must not check isatty
    assert "isatty" not in source           # ← failed on the comment explaining its absence

The first is the dangerous direction. A test that asserts a string **is present** in source is
satisfied by any mention of it — including the comment the author wrote to explain why the code
matters. It reports a property nobody is holding, which is worse than no test at all: it is a
green light attached to nothing.

`code_of` strips comments and docstrings, so an assertion lands on code. `test_tests_read_code.py`
requires it wherever the dangerous shape appears, because a convention nobody checks is a
convention that lasts one refactor.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

__all__ = ["code_of", "strip_comments"]

_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_LINE = re.compile(r"(^|\s)//.*$", re.M)


def _strip_python(text: str) -> str:
    """Comments and docstrings out, everything else byte-identical in position.

    Tokenised rather than regexed: `"# not a comment"` inside a string literal is ordinary code, and
    a regex that removed it would make assertions fail on code that is really there — trading a
    false pass for a false failure, which is not an improvement.
    """
    out: list[str] = []
    previous_end = (1, 0)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text  # unparseable is the test's problem, not this helper's

    prev_type = tokenize.INDENT
    for token in tokens:
        if token.start[0] > previous_end[0]:
            previous_end = (token.start[0], 0)
            out.append("\n" * (token.start[0] - len(out and "".join(out).splitlines()) or 0))
        # A string that is the whole statement is a docstring; anything else is a value.
        docstring = token.type == tokenize.STRING and prev_type in {
            tokenize.INDENT,
            tokenize.NEWLINE,
            tokenize.NL,
            tokenize.DEDENT,
        }
        if token.type == tokenize.COMMENT or docstring:
            out.append(" " * (token.end[1] - token.start[1]))
        else:
            out.append(token.string)
        if token.type not in {tokenize.NL, tokenize.COMMENT}:
            prev_type = token.type
    return "".join(out)


def strip_comments(text: str, suffix: str) -> str:
    """Comments out, for the languages this repository actually contains."""
    if suffix == ".py":
        return _strip_python(text)
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".css"}:
        return _LINE.sub(r"\1", _BLOCK.sub("", text))
    return text


def code_of(path: str | Path) -> str:
    """A source file with its comments and docstrings removed.

    Use this — not `read_text` — whenever a test asserts that something **is** in source. The
    string you are looking for is almost always also in the comment explaining it.
    """
    p = Path(path)
    return strip_comments(p.read_text(encoding="utf-8"), p.suffix)
