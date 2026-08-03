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
# The control plane used to be the second entry here. It lives in the `neti-cloud` repository now
# and carries its own copy of this check; see LICENSING.md.
PACKAGES = [REPO / "src" / "neti"]

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


# --------------------------------------------------------------------------- optional extras
#
# The same defect one dependency over, and it had been shipping. `httpx` is not a base requirement —
# it lives in the `graph` and `mcp` extras — and `resolvers/graph_client.py` imported it at module
# scope. That file is reached by `engine` -> `registry`, so `pip install neti` followed by
# `import neti` raised `ModuleNotFoundError: httpx`: the package's entire public surface, and the
# `Preflight.from_config` example the README leads with, unusable on a plain install.
#
# Nothing could have caught it, for the same reason nothing caught `fcntl`: every environment the
# suite runs in installs the extras.

# Which import belongs to which extra. `pip install neti` gets none of them.
OPTIONAL_EXTRAS = {
    "httpx": "graph, mcp",
    "msal": "graph",
    "typer": "cli",
    "rich": "cli",
    "fastapi": "console",
    "uvicorn": "console",
    "boto3": "storage",
    "psycopg": "database",
}


# The modules a plain `import neti` pulls in. Everything else in the package is reached only through
# a CLI command, the console, or an explicit import by somebody who has installed the extra.
def _import_time_modules() -> list[Path]:
    """Which files a fresh `import neti` actually loads.

    In a subprocess, and that is not fussiness. The obvious version — drop `neti.*` from
    `sys.modules` and re-import — runs at collection time and hands every later test a second copy
    of every class, so `isinstance` starts failing across the suite. It broke 258 tests before this
    comment existed. A clean interpreter is also the more faithful measurement: it is exactly what
    somebody's `python -c "import neti"` does.
    """
    import json
    import subprocess
    import sys

    probe = (
        "import json, sys; import neti; "
        "print(json.dumps(sorted(m.__file__ for n, m in sys.modules.items() "
        "if (n == 'neti' or n.startswith('neti.')) and getattr(m, '__file__', None))))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, check=True
    )
    return [Path(p) for p in json.loads(out.stdout)]


@pytest.mark.parametrize("source", _import_time_modules(), ids=lambda p: str(p.name))
def test_importing_neti_needs_no_optional_extra(source: Path) -> None:
    """Every module `import neti` reaches must be importable with the base dependencies alone.

    Asserted over the modules actually loaded rather than over the whole package, because the rule
    is not "never import httpx at module scope" — `gateway/upstream.py` legitimately does, and you
    cannot reach it without the `mcp` extra anyway. The rule is that the *import graph rooted at
    `neti`* stays inside the base dependencies, which is what `pip install neti` promises.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    offenders = {
        name: OPTIONAL_EXTRAS[name]
        for name in _module_scope_imports(tree)
        if name in OPTIONAL_EXTRAS
    }
    assert not offenders, (
        f"{source.relative_to(REPO)} is loaded by `import neti` and imports {offenders} at "
        "module scope, so a plain `pip install neti` raises ModuleNotFoundError. Defer it into "
        "the function that needs it — see `GraphClient._client`."
    )


def test_the_public_surface_works_with_no_extras_at_all() -> None:
    """The other half: having banned the import, the thing it was for must still work.

    A package that satisfied the rule by deleting `GraphClient` would pass the test above. This is
    the README's own first example — a filesystem policy, no credential, no directory — reaching a
    real verdict, which is what a plain install has to be able to do.
    """
    from neti import Preflight

    pf = Preflight.from_config(str(REPO / "examples" / "coding-agent.yaml"), records=None)
    verdict = pf.check("Read", {"file_path": str(REPO / "README.md")})
    assert verdict.payload["resolved"] == 1
