"""Invariant 4: `neti.core` performs no I/O, reads no clock, and consults no environment.

This is not stylistic. The replay contract in RESOLVER_CONTRACT.md rule 3 depends on it: if a
deadline, a `now()` or an env var leaks into the decision, a stored decision stops reproducing and
the audit record's whole claim collapses.

Two checks, because neither alone is sufficient. The import check catches a dependency on an I/O
library; the source check catches a direct call to something ambient that arrives via an
already-permitted import (`datetime` is legitimately imported for its *type*).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "src" / "neti" / "core"

# Checked against the imports declared in our own source, NOT against sys.modules.
#
# An earlier version asserted on sys.modules after importing neti.core and failed on `os.path`,
# `random` and `socket` — all pulled in by pydantic and by interpreter startup, none of them ours.
# That test was measuring our dependencies' behaviour instead of our own, which is both unfixable
# and beside the point: the guarantee we owe is that *this package's code* reaches nothing outward.
FORBIDDEN_IMPORTS = {
    "socket",
    "ssl",
    "http",
    "urllib",
    "httpx",
    "requests",
    "subprocess",
    "asyncio",
    "sqlite3",
    "random",
    "secrets",
    "os",
    "pathlib",
    "time",
    "uuid",
    "logging",
}

# Permitted and why: `datetime` for the type on Resolution.resolved_at (never called),
# `hashlib` for pure hashing, `unicodedata` for NFC, `enum`/`typing`/`collections.abc`/`pydantic`.

# Ambient reads. Each maps to why it would break the replay contract.
FORBIDDEN_CALLS = {
    "datetime.now": "a decision must be timestamped by its caller, not by itself",
    "datetime.utcnow": "same, and utcnow is deprecated besides",
    "time.time": "no wall-clock in the decision path",
    "time.monotonic": "no wall-clock in the decision path",
    "os.getenv": "configuration arrives as arguments, not from the environment",
    "os.environ": "configuration arrives as arguments, not from the environment",
    "uuid.uuid4": "identifiers are supplied so the record is reproducible",
    "random.random": "no nondeterminism",
}


def test_core_declares_no_io_capable_imports() -> None:
    offenders: list[str] = []
    for path in sorted(CORE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots = [node.module.split(".")[0]]
            offenders.extend(
                f"{path.name}: imports {root}" for root in roots if root in FORBIDDEN_IMPORTS
            )
    assert not offenders, "neti.core reaches outward:\n" + "\n".join(offenders)


def _qualified_call_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = []
            cur: ast.expr = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                names.add(".".join(reversed(parts)))
    return names


def test_core_makes_no_ambient_reads() -> None:
    offenders: list[str] = []
    for path in sorted(CORE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        used = _qualified_call_names(tree)
        for forbidden, why in FORBIDDEN_CALLS.items():
            # match both `datetime.now` and `datetime.datetime.now`
            if any(name == forbidden or name.endswith("." + forbidden) for name in used):
                offenders.append(f"{path.name}: {forbidden} — {why}")
    assert not offenders, "ambient reads in neti.core:\n" + "\n".join(offenders)


def test_core_has_no_module_level_mutable_state() -> None:
    """Module-level mutable state is how a pure module quietly becomes stateful across calls."""
    offenders: list[str] = []
    for path in sorted(CORE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign | ast.AnnAssign):
                continue
            value = node.value
            if isinstance(value, ast.Dict | ast.List) and (
                value.keys if isinstance(value, ast.Dict) else value.elts
            ):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if not isinstance(t, ast.Name):
                        continue
                    # UPPER_CASE is a declared constant; dunders like __all__ are module metadata.
                    if t.id.isupper() or t.id.startswith("__"):
                        continue
                    offenders.append(f"{path.name}: {t.id}")
    assert not offenders, f"module-level mutable state: {offenders}"


# ---------------------------------------------------------------------------- the policy digest


def test_the_policy_digest_is_stable_across_processes() -> None:
    """Invariant: the same policy file hashes the same everywhere, always.

    This is load-bearing in three places at once. The digest is stamped into every decision record,
    so two agents on one config must not record two different policies. `neti verify` replays
    against it. And an approval is bound to it, so a wobbling digest means a grant can never be
    redeemed by the process that asked for it.

    It was not stable. `BudgetRule.tools` is a `frozenset`, and a frozenset serialises in *hash*
    order — so a policy with a two-tool session budget produced one digest under PYTHONHASHSEED=0
    and another under =1. The CI matrix runs exactly those seeds and never caught it, because
    nothing hashed a policy inside a test; it surfaced when a live approval refused to match itself
    across a retry.

    Run out-of-process on purpose: within one interpreter the seed is fixed and this passes
    vacuously, which is precisely how the bug survived.
    """
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    script = (
        "from neti.config.policy import load_policy;"
        f"print(load_policy({str(root / 'examples' / 'entra.yaml')!r}).digest())"
    )
    digests = {
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            # The inherited environment with one variable overridden, not a hand-built minimal one.
            # A minimal `{PYTHONHASHSEED, PATH}` is enough on Unix and not enough on Windows, where
            # the interpreter needs SYSTEMROOT to initialise at all and exits non-zero before
            # running a line of this script. What the test varies is the seed; everything else it
            # was accidentally varying too.
            env={**os.environ, "PYTHONHASHSEED": seed},
            cwd=root,
        ).stdout.strip()
        for seed in ("0", "1", "2", "3")
    }
    assert len(digests) == 1, f"the policy digest depends on PYTHONHASHSEED: {digests}"
