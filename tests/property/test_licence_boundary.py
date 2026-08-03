"""Invariant 5: the free package never reaches into the paid one.

LICENSING.md makes a promise that is easy to write and easy to erode: there are no licence checks in
this code, and the entitlement is possession of a control plane rather than a key someone validates.
That promise breaks the first time a single `import neti_cloud` appears under `src/neti/` — at that
moment the free tier has a dependency on the paid one, and either it degrades without it or someone
is tempted to make it.

The two are separate distributions with separate licences, and now separate repositories, so this is
a packaging invariant as well as an ethical one: BUSL code must never end up inside a wheel whose
metadata says Apache-2.0.

Checked against the imports declared in our own source rather than against `sys.modules`, for the
same reason `test_core_is_pure.py` gives: measuring what our dependencies happen to load is both
unfixable and beside the point.

**The other half of this boundary is asserted in the other repository.** `neti_cloud` must never
import the decision machinery — `neti.core.decide`, `neti.core.budget`, `neti.engine`,
`neti.gatekeeper` — because a server-side ceiling comparison would mean two places decide and the
audit record would describe only one of them. That test used to live in this file, guarded by
`if not PAID.exists(): return`. Once the control plane moved out, that guard was always true and the
test would have passed forever without reading a line of the code it claims to check, which is worse
than not having it. It now lives in `neti-cloud`, in
`tests/property/test_the_control_plane_never_decides.py`, where the files it reads actually are.

So the boundary is checked from both sides, and neither side can go vacuous without the source it
reads disappearing from the repository it lives in.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "src"
FREE = ROOT / "neti"

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


def test_the_paid_package_is_not_in_this_repository() -> None:
    """The split, asserted rather than assumed.

    Every claim this file makes about a boundary between two repositories is worth nothing if the
    control plane quietly reappears in this one — at which point `pip install neti` could start
    shipping BUSL source inside a wheel whose metadata says Apache-2.0, and the reader who was told
    "one repository, one licence" would be wrong without anyone noticing.
    """
    strays = [
        p.relative_to(REPO)
        for p in REPO.rglob("neti_cloud")
        if p.is_dir() and ".git" not in p.parts
    ]
    assert not strays, (
        "the control plane lives in the `neti-cloud` repository — see LICENSING.md:\n  "
        + "\n  ".join(str(s) for s in strays)
    )
