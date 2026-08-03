"""Invariant: every text file this package reads or writes names its encoding.

`Path.read_text()` with no `encoding=` uses `locale.getpreferredencoding()`. On macOS and Linux that
is UTF-8 and nothing ever goes wrong. On Windows it is cp1252, and the first byte above 0x7f raises
`UnicodeDecodeError` on a line that has worked perfectly for everyone who wrote it.

This was not hypothetical. `neti init` reads the MCP client configs already on the machine, through
`discover.py`, and it carries a Windows branch for finding Claude Desktop's config file precisely
because we expect to run there. That read had no encoding. A config holding one accented character
in a path, one non-English server description, one em-dash in a comment, and `neti init` would die
on Windows with a stack trace about charmap. The suite was green on two platforms and could not see
it; the Windows CI job found it the first time this repository was pushed.

It is the same defect as `test_platform_imports.py` one layer up: behaviour that depends on the
machine it ran on, invisible to everyone whose machine happens to agree with the author's.

So the rule is mechanical and this makes it enforceable: if it reads or writes text, it says which
encoding. `read_bytes` and `write_bytes` are untouched, because bytes have no encoding to get wrong.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# The tests are checked too, and not for tidiness: the Windows CI job is the only thing that runs
# this suite on a machine where the default encoding is not UTF-8, so a test that reads a fixture
# without an encoding turns that job red and hides whatever it was supposed to be reporting. That is
# exactly what happened. `examples/entra.yaml` has a box-drawing character in a comment, five test
# modules read it, and the first push to this repository failed on all of them at once.
ROOTS = (REPO / "src" / "neti", REPO / "tests", REPO / "tools", REPO / "eval")

# The built console is a Next export dropped in by `just console-sync`. It is not our source, it is
# not Python, and it is gitignored.
SKIP = ("console",)

# `Path.read_text` / `Path.write_text` are matched as attributes. `open` is matched only as a bare
# name: `webbrowser.open(url)` is not file IO, and an earlier version of this test flagged it, which
# is a good reminder that a check keen enough to be useful is keen enough to be wrong.
TEXT_METHODS = {"read_text", "write_text"}

# Temporary files have exactly the same problem and are easier to miss, because the mode string
# is the first positional argument rather than a keyword. A test wrote a generated policy through
# one of these and then read it back with `load_policy`, which does name UTF-8 — so Windows wrote
# cp1252 and the product's own correct reader was the thing that raised.
TEMPFILES = {"NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"}


def _sources() -> list[Path]:
    return sorted(
        p for root in ROOTS for p in root.rglob("*.py") if not any(s in p.parts for s in SKIP)
    )


def _mode(node: ast.Call, positional: int) -> str:
    """The mode string, from wherever it was passed. Unknown modes read as text, which is the
    default and the one that needs an encoding."""
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    args = node.args[positional : positional + 1]
    if args and isinstance(args[0], ast.Constant):
        return str(args[0].value)
    return ""


def _offenders(tree: ast.Module) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if isinstance(node.func, ast.Attribute) and node.func.attr in TEXT_METHODS:
            name, mode = node.func.attr, ""
        elif isinstance(node.func, ast.Name) and node.func.id == "open":
            name, mode = "open", _mode(node, positional=1)
        elif isinstance(node.func, ast.Attribute) and node.func.attr in TEMPFILES:
            name, mode = node.func.attr, _mode(node, positional=0) or "w+b"
        elif isinstance(node.func, ast.Name) and node.func.id in TEMPFILES:
            name, mode = node.func.id, _mode(node, positional=0) or "w+b"
        else:
            continue

        if "b" in mode:  # bytes have no encoding to get wrong
            continue
        if not any(k.arg == "encoding" for k in node.keywords):
            out.append(f"line {node.lineno}: {name}(...) with no encoding=")
    return out


def test_there_are_sources_to_check() -> None:
    """Guards against a path typo turning this file into a no-op."""
    assert len(_sources()) > 100


@pytest.mark.parametrize("source", _sources(), ids=lambda p: str(p.relative_to(REPO)))
def test_every_text_read_and_write_names_its_encoding(source: Path) -> None:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    offenders = _offenders(tree)
    assert not offenders, (
        f"{source.relative_to(REPO)} reads or writes text without an encoding, so it decodes as "
        "cp1252 on Windows and raises on the first byte above 0x7f:\n  " + "\n  ".join(offenders)
    )
