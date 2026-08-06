"""What to put in front of the gate, on *this* machine.

The console's live gate offers a tool and a target and fires a real call. The tool list was once
four hardcoded Entra names, so a coding-agent install was offered `remove_group_members` in its own
console; that got fixed. The target list is the same bug one field over and it survived: it was the
synthetic tenant's groups, which are `null` for any policy that binds no directory — so the dropdown
was empty, the button did nothing, and **the page that exists to demonstrate the product was dead
for the most common install there is.**

So targets are derived from the resolvers the policy actually binds, off the directory it actually
declares. Three rules:

**Real, or absent.** Every suggestion here is a path that exists or a command that would run. A
plausible-looking placeholder in a box labelled *fire your own* is worse than an empty box, because
the number that comes back is about nothing.

**Cheap.** One shallow `scandir` of the root, never a walk. This is a dropdown, and the walk is what
the resolver does *after* somebody picks something.

**Chosen to show the shape.** For the shell that means one command of each kind — sized, recognised
but unsizeable, and not a deletion at all — because that trichotomy is the thing about `shell.paths`
that a person has to see once to understand.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["Target", "targets_for"]


@dataclass(frozen=True)
class Target:
    value: str
    """Exactly what gets sent as the gated argument."""

    label: str
    """What it means, for somebody choosing. Never a number — the number is the gate's answer."""

    def as_json(self) -> dict[str, str]:
        return {"value": self.value, "label": self.label}


def _root(providers: dict[str, Any]) -> Path:
    declared = (providers.get("fs") or {}).get("root")
    return Path(str(declared)) if declared else Path.cwd()


def _children(root: Path, *, limit: int = 4) -> list[Path]:
    """A few real directories under the root, biggest-looking first.

    Sorted by name rather than by size: sizing them is exactly the question the gate is about to be
    asked, and answering it here to populate a dropdown would be doing the expensive thing twice.
    """
    try:
        entries = [
            Path(e.path)
            for e in os.scandir(root)
            if e.is_dir() and not e.name.startswith(".") and e.name != "__pycache__"
        ]
    except OSError:
        return []
    return sorted(entries, key=lambda p: p.name)[:limit]


def _first_file(root: Path) -> Path | None:
    try:
        for entry in os.scandir(root):
            if entry.is_file() and not entry.name.startswith("."):
                return Path(entry.path)
    except OSError:
        return None
    return None


def _fs_targets(root: Path) -> list[Target]:
    out = [
        Target(f"{root}/**/*", "everything under the declared root"),
        Target(str(root), "the root itself"),
    ]
    out += [Target(str(child), f"the {child.name} directory") for child in _children(root)]
    one = _first_file(root)
    if one is not None:
        # The small end matters as much as the large one: a gate that only ever demonstrates a
        # blocked call has not shown that ordinary work passes untouched.
        out.append(Target(str(one), "a single file — the ordinary case"))
    return out


def _shell_targets(root: Path) -> list[Target]:
    """One command of each kind, because the three-way split is the whole point of `shell.paths`."""
    children = _children(root, limit=1)
    sizeable = f"rm -rf {children[0]}" if children else f"rm -rf {root}"
    return [
        Target(sizeable, "a deletion it can size — the bands decide"),
        Target(
            "cat list.txt | xargs rm",
            "a deletion it cannot size — recognised, flagged, and it runs",
        ),
        Target("npm test", "not a deletion at all — silent"),
        Target("git clean -fdx", "a deletion it can size, through git"),
    ]


def targets_for(
    tool: str,
    resolvers: list[str],
    *,
    providers: dict[str, Any] | None = None,
    fixture: list[dict[str, Any]] | None = None,
) -> list[Target]:
    """Suggested targets for one gated tool, from the resolvers behind it.

    `fixture` is the synthetic tenant, and it is used only where a directory resolver is actually
    bound — which is the fix. Passing it for a filesystem policy was how a coding agent came to be
    offered "All Engineering (nested) — 41,203" as something to point `Glob` at: groups that do not
    exist, in a picker headed *fire your own*.
    """
    root = _root(providers or {})

    out: list[Target] = []
    for resolver in resolvers:
        if resolver.startswith("entra."):
            out += [Target(str(g["id"]), f"{g['name']} — {g['members']:,}") for g in fixture or []]
        elif resolver == "shell.paths":
            out += _shell_targets(root)
        elif resolver == "fs.paths":
            out += _fs_targets(root)
        # Anything else — `db.rows`, `storage.objects`, `github.*`, `terraform.destroy` — needs a
        # statement, a URI or a plan file that only the operator has. Suggesting a shape would be
        # suggesting a target that does not exist, so those tools get an empty list and the console
        # offers a free-text field instead.

    seen: set[str] = set()
    unique: list[Target] = []
    for target in out:
        if target.value not in seen:
            seen.add(target.value)
            unique.append(target)
    del tool
    return unique
