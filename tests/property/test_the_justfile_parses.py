"""The justfile is the documented way in, so it has to be a file `just` will read.

`just` treats a redefined recipe as a parse error, not an override. A second `conformance:` was
added and from that commit *every* `just` command in this repository failed — `just test`, `just
check`, even `just --list` — with an error naming a recipe most people were not trying to run.
CONTRIBUTING sends a new contributor to `just install` as their first instruction.

Nothing caught it because nothing here runs `just`: CI calls `uv run pytest` directly, and so does
everybody who has already got the repository working. The people it broke were exactly the ones with
no way to know it was not their fault.

Parsing needs the binary, which is not a dependency of this project and should not become one — the
justfile is a convenience, not part of the product. So the recipe *names* are checked here in plain
Python, which is what the failure actually was, and the full parse is attempted only where `just`
happens to exist.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
JUSTFILE = REPO / "justfile"

RECIPE = re.compile(r"^([a-z][a-z0-9-]*)(?:\s+[^:\n]*)?:", re.M)
"""A recipe line: a name at column zero, optional parameters, then a colon.

Deliberately not matching indented lines — a colon inside a recipe body is a shell command, not a
declaration, and matching those would make this fail on `uv run neti prove -c examples/entra.yaml`.
"""


def _names() -> list[str]:
    # Comments first: `# card: something` at column zero is prose, and the pattern above cannot
    # tell it from a declaration.
    body = re.sub(r"^\s*#.*$", "", JUSTFILE.read_text(encoding="utf-8"), flags=re.M)
    return RECIPE.findall(body)


def test_no_recipe_is_defined_twice() -> None:
    names = _names()
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert not duplicates, (
        "these recipes are defined more than once, which `just` treats as a parse error — every "
        f"command in the justfile fails, not only these: {duplicates}"
    )


def test_there_are_recipes_to_check() -> None:
    """Guards the pattern above: a regex that silently matches nothing passes the test above."""
    names = _names()
    assert len(names) > 20, f"the recipe pattern found only {names}, which cannot be right"
    assert "test" in names and "install" in names, (
        "the two recipes CONTRIBUTING names first are not being found, so this file is checking "
        f"something other than what it thinks: {sorted(names)}"
    )


@pytest.mark.skipif(shutil.which("just") is None, reason="just is not installed here")
def test_just_itself_can_parse_it() -> None:
    """The real thing, where it is available. `--summary` parses without running anything."""
    done = subprocess.run(
        ["just", "--justfile", str(JUSTFILE), "--summary"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert done.returncode == 0, f"just cannot parse the justfile:\n{done.stderr}"
