"""Invariant 6: every command the documentation shows exists and takes the flags it is shown with.

`neti report --since 7d` appeared in the README from the first draft. It did not exist. Typing the
documented command printed a usage error, and the suite was green the entire time — because nothing
anywhere connected the prose to the code.

That is a whole class, not one typo. Documentation drifts silently by construction: the code moves,
the markdown does not, and the only detector is a person reading the README and typing what it says.
This test is that person, run on every commit.

**What it asserts and what it deliberately does not.** It checks that each documented subcommand
exists and that every `--flag` shown with it is real. It does *not* assert the output matches — the
README's numbers are illustrative (`412 people`, `41,203`) and pinning them would make the docs
unmaintainable and the test a liar's contract. What the CLI *says* is covered by the golden
transcripts instead; this file only guarantees the reader can run what they read.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.main import get_command

from neti.cli import app

REPO = Path(__file__).resolve().parents[2]
DOCS = ["README.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSING.md", "SCOPE.md", "SECURITY.md"]

# `$ neti …` in a console block, or `neti …` inside backticks. Both appear in these files and both
# are things a reader will copy.
_INVOCATION = re.compile(r"(?:^\s*\$\s*|`)(neti(?:-cloud)?\s+[a-z][\w-]*(?:[^`\n]*))", re.M)
_FLAG = re.compile(r"(?<![\w-])(--[a-z][\w-]*)")

# Placeholders a reader is expected to substitute. A doc line containing one is checked for its
# command and flags, never run.
_PLACEHOLDER = re.compile(r"[<${]|\.\.\.|your-|acme|example\.com|mcp\.internal")


def _documented() -> list[tuple[str, str, str]]:
    """`(source file, whole invocation, subcommand)` for everything the docs tell you to run."""
    found: list[tuple[str, str, str]] = []
    for name in DOCS:
        path = REPO / name
        if not path.is_file():
            continue
        for match in _INVOCATION.finditer(path.read_text(encoding="utf-8")):
            invocation = match.group(1).strip().rstrip("`")
            parts = invocation.split()
            if len(parts) >= 2:
                found.append((name, invocation, parts[1]))
    return found


def _commands() -> dict[str, set[str]]:
    """Every subcommand of `neti`, and the flags it accepts."""
    group = get_command(app)
    out: dict[str, set[str]] = {}
    for name, command in getattr(group, "commands", {}).items():
        flags: set[str] = set()
        for param in command.params:
            flags.update(o for o in getattr(param, "opts", []) if o.startswith("--"))
            flags.update(o for o in getattr(param, "secondary_opts", []) if o.startswith("--"))
        out[name] = flags
    return out


def test_the_docs_show_at_least_one_command() -> None:
    """If the extractor silently matches nothing, every test below passes vacuously."""
    assert len(_documented()) >= 5


@pytest.mark.parametrize(
    ("source", "invocation", "subcommand"),
    [pytest.param(*d, id=f"{d[0]}:{d[1][:48]}") for d in _documented()],
)
def test_every_documented_command_exists(source: str, invocation: str, subcommand: str) -> None:
    commands = _commands()

    if invocation.startswith("neti-cloud"):
        pytest.skip("neti-cloud is a separate distribution; checked by its own suite")

    assert subcommand in commands, (
        f"{source} tells the reader to run `{invocation}`, and `neti {subcommand}` does not exist. "
        f"Available: {', '.join(sorted(commands))}"
    )

    shown = set(_FLAG.findall(invocation))
    unknown = shown - commands[subcommand]
    assert not unknown, (
        f"{source} shows `{invocation}`, but `neti {subcommand}` does not accept "
        f"{', '.join(sorted(unknown))}. This is how `--since` shipped in the README for months "
        f"without existing. Accepted: {', '.join(sorted(commands[subcommand])) or 'none'}"
    )


def test_the_commands_a_reader_meets_first_are_all_documented() -> None:
    """The other direction, and a weaker claim on purpose.

    Not every command needs to appear in the README — `measure` and `check` are for validating a
    tenant, not for a first read. But the journey the README sells has to be complete: if `init`
    stopped being documented, the first five minutes would have no entry point and nothing would
    notice.
    """
    documented = {sub for _, _, sub in _documented()}
    journey = {"init", "inventory", "gate", "report", "propose", "verify"}
    missing = journey - documented
    assert not missing, f"the README no longer shows: {', '.join(sorted(missing))}"
