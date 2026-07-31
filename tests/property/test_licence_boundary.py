"""Invariant 5: the free package never reaches into the paid one.

LICENSING.md makes a promise that is easy to write and easy to erode: there are no licence checks in
the Apache-2.0 code, and the entitlement is possession of a control plane rather than a key someone
validates. That promise breaks the first time a single `import neti_cloud` appears under `src/neti/`
— at that moment the free tier has a dependency on the paid one, and either it degrades without it
or someone is tempted to make it.

The reverse direction is fine and expected: `neti_cloud` imports `neti` freely. It is a server for
the gate, not a fork of it.

Checked against the imports declared in our own source rather than against `sys.modules`, for the
same reason `test_core_is_pure.py` gives: measuring what our dependencies happen to load is both
unfixable and beside the point.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src"
FREE = ROOT / "neti"
PAID = ROOT / "neti_cloud"

FORBIDDEN = "neti_cloud"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_the_free_package_never_imports_the_paid_one() -> None:
    offenders: list[str] = []
    for source in sorted(FREE.rglob("*.py")):
        for name in _imported_modules(source):
            if name == FORBIDDEN or name.startswith(f"{FORBIDDEN}."):
                offenders.append(f"{source.relative_to(ROOT)} imports {name}")

    assert not offenders, (
        "the Apache-2.0 package must not depend on the BUSL one — see LICENSING.md:\n  "
        + "\n  ".join(offenders)
    )


def test_the_free_package_holds_no_licence_check() -> None:
    """No key validation, no kill switch, no phone-home.

    A grep is a blunt instrument, but the thing it is guarding is a promise about intent, and the
    words below are what that intent looks like when it starts to slip.
    """
    tells = ("licence_key", "license_key", "check_licence", "check_license", "is_licensed")
    offenders = [
        f"{source.relative_to(ROOT)}: {tell}"
        for source in sorted(FREE.rglob("*.py"))
        for tell in tells
        if tell in source.read_text(encoding="utf-8")
    ]
    assert not offenders, "the free tier is not gated by a key:\n  " + "\n  ".join(offenders)


def test_the_paid_package_may_import_the_free_one() -> None:
    """Stated as a test so nobody 'fixes' the boundary by making it symmetric."""
    if not PAID.exists():
        return
    assert any(
        name == "neti" or name.startswith("neti.")
        for source in PAID.rglob("*.py")
        for name in _imported_modules(source)
    ), "the control plane is a server for the gate; it should be built on it, not duplicate it"
