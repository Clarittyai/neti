"""Invariant 7: nothing in the package imports a platform-only stdlib module at import time.

`import fcntl` went into the record sink on a macOS laptop, to lock the file across the processes a
`PreToolUse` hook creates. `fcntl` does not exist on Windows, so `import neti` would have raised on
every Windows machine — and `neti init` carries a Windows branch for finding Claude Desktop's
config, so we plainly expect to run there. CI was ubuntu and macos, the suite was green, and nothing
could have caught it.

The fix was to branch on `sys.platform` *inside* the function. This test makes that the only option
available, permanently, in the same shape as the `neti.core` purity check: parse our own source and
look at what it declares, rather than measure what happened to load in this interpreter.

A module-scope import is the thing that breaks the package. A function-scope one is fine — it only
runs on the platform that has it — so the check is deliberately about *where* the import sits.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PACKAGES = [REPO / "src" / "neti", REPO / "cloud" / "src" / "neti_cloud"]

# Unix-only or Windows-only, all of them plausible things to reach for in a file-locking, terminal
# or process-limits context — which is exactly the code that tends to get written on one machine.
PLATFORM_ONLY = {
    "fcntl": "Unix only",
    "termios": "Unix only",
    "pwd": "Unix only",
    "grp": "Unix only",
    "resource": "Unix only",
    "msvcrt": "Windows only",
    "winreg": "Windows only",
}


def _module_scope_imports(tree: ast.Module) -> set[str]:
    """Top-level imports only. Anything nested inside a def, class or `if` is not import-time."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _sources() -> list[Path]:
    return sorted(p for pkg in PACKAGES if pkg.exists() for p in pkg.rglob("*.py"))


def test_there_are_sources_to_check() -> None:
    """Guards against a path typo turning this whole file into a no-op."""
    assert len(_sources()) > 20


@pytest.mark.parametrize("source", _sources(), ids=lambda p: str(p.name))
def test_no_platform_only_import_at_module_scope(source: Path) -> None:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    offenders = {
        name: PLATFORM_ONLY[name] for name in _module_scope_imports(tree) if name in PLATFORM_ONLY
    }
    assert not offenders, (
        f"{source.relative_to(REPO)} imports {offenders} at module scope, so `import neti` raises "
        "on every other platform. Branch on sys.platform inside the function that needs it — see "
        "`_exclusive` in neti/store/jsonl.py."
    )


def test_the_lock_still_reaches_for_both(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half: having banned the top-level import, the platform code must still be there.

    A file that satisfies the rule by simply not locking anything would pass the test above and
    silently reintroduce the forked chain. This asserts both branches exist and are selected by
    `sys.platform`, so the invariant cannot be met by deleting the feature.
    """
    from neti.store import jsonl

    body = Path(jsonl.__file__).read_text(encoding="utf-8")
    assert 'sys.platform == "win32"' in body
    assert "import msvcrt" in body
    assert "import fcntl" in body
