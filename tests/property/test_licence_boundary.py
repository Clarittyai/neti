"""Invariant 5: the free package never reaches into the paid one.

LICENSING.md makes a promise that is easy to write and easy to erode: there are no licence checks in
the Apache-2.0 code, and the entitlement is possession of a control plane rather than a key someone
validates. That promise breaks the first time a single `import neti_cloud` appears under `src/neti/`
— at that moment the free tier has a dependency on the paid one, and either it degrades without it
or someone is tempted to make it.

The two are separate distributions with separate licences (`pyproject.toml` and
`cloud/pyproject.toml`), so this is a packaging invariant as well as an ethical one: BUSL code must
never end up inside a wheel whose metadata says Apache-2.0.

Checked against the imports declared in our own source rather than against `sys.modules`, for the
same reason `test_core_is_pure.py` gives: measuring what our dependencies happen to load is both
unfixable and beside the point.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "src"
FREE = ROOT / "neti"
# A second distribution entirely — see cloud/pyproject.toml. Shipping BUSL code inside a wheel whose
# metadata says Apache-2.0 would be a licence misstatement, and metadata is what auditors read.
PAID = REPO / "cloud" / "src" / "neti_cloud"

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


# The decision procedure. Every ceiling comparison, every verdict join, every budget tally.
DECISION_MACHINERY = ("neti.core.decide", "neti.core.budget", "neti.engine", "neti.gatekeeper")


def test_the_control_plane_never_decides() -> None:
    """The server records who said yes. It does not work out whether the call was too big.

    This is the invariant that keeps "the decision is made locally, deterministically, from a policy
    you can read" true once a network is involved. The control plane sees a request digest and the
    evidence a human needs; it never sees the arguments, and it must never acquire the ability to
    reach its own verdict — a server-side ceiling comparison would mean two places decide, and the
    audit record would only describe one of them.

    An earlier version of this file asserted the opposite of something useful: that `neti_cloud`
    *must* import `neti`, on the theory that a server for the gate should be built on it. It turned
    out the control plane needs nothing from the gate — it deals in digests and evidence — and the
    looser coupling is better, not worse. This is the assertion that was actually worth making.
    """
    if not PAID.exists():
        return

    offenders = [
        f"{source.relative_to(REPO)} imports {name}"
        for source in sorted(PAID.rglob("*.py"))
        for name in _imported_modules(source)
        if name in DECISION_MACHINERY
    ]
    assert not offenders, (
        "the control plane must not be able to reach a verdict of its own:\n  "
        + "\n  ".join(offenders)
    )
