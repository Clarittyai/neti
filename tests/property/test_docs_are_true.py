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


def test_the_readme_resolver_table_matches_the_registry() -> None:
    """The catalogue a reader uses to decide whether this covers their stack.

    A resolver missing from the table is invisible to everyone who does not read the source; a
    resolver in the table that no longer exists is a promise the product breaks at policy-load.
    Both are the docs-drift class the `--since` defect belonged to, so both are a diff a human sees
    rather than something anybody has to remember.
    """
    import re

    from neti.resolvers.graph_client import ClientCredential, GraphClient
    from neti.resolvers.registry import resolvers_for_client

    readme = (REPO / "README.md").read_text(encoding="utf-8")
    table = readme.split("## What can be sized", 1)[1].split("##", 1)[0]
    documented = set(re.findall(r"^\| `([a-z_]+\.[a-z_]+)` \|", table, re.MULTILINE))

    client = GraphClient(ClientCredential(tenant_id="t", client_id="c", client_secret="s"))
    registered = set(resolvers_for_client(client))

    assert documented, "the resolver table has changed shape — this test cannot see it any more"
    assert not documented - registered, (
        f"README promises resolvers that are not registered: {sorted(documented - registered)}"
    )
    assert not registered - documented, (
        f"shipped but undocumented, so nobody will find them: {sorted(registered - documented)}"
    )


def test_the_record_size_the_readme_publishes_is_the_size_records_actually_are() -> None:
    """The one number in the cost table that can be checked offline, so it is.

    It said `~700 bytes per call` and the real figure is a little over a kilobyte — measured, not
    modelled, and wrong in the flattering direction for as long as nobody re-measured. That is a
    small error about disk and a large one about the posture: this project asks people to check its
    numbers, so its own published numbers have to survive being checked.

    The claim is a range because record size genuinely varies — most of a record is `causes`, the
    per-argument evidence that makes a verdict re-derivable, plus whatever `args` the call carried,
    so it moves with how long the operator's paths are. The range is what is defensible; a single
    figure would be precise about something that is not.
    """
    import tempfile

    from neti.config.policy import load_policy
    from neti.core.types import ProposedCall
    from neti.engine import Engine
    from neti.gatekeeper import Gatekeeper
    from neti.resolvers.graph_client import ClientCredential, GraphClient
    from neti.resolvers.registry import resolvers_for_client
    from neti.store.jsonl import JsonlSink

    low, high = _published_record_size()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tree").mkdir()
        for i in range(5):
            (root / "tree" / f"f{i}.txt").write_text("x", encoding="utf-8")

        policy = load_policy(str(REPO / "examples" / "coding-agent.yaml"))
        blank = GraphClient(ClientCredential(tenant_id="", client_id="", client_secret=""))
        records = root / "d.ndjson"
        sink = JsonlSink(records)
        gate = Gatekeeper(
            engine=Engine(policy=policy, resolvers=resolvers_for_client(blank, policy.providers)),
            sink=sink,
        )
        try:
            for tool, args in (
                ("Read", {"file_path": str(root / "tree" / "f0.txt")}),
                ("Edit", {"file_path": str(root / "tree" / "f1.txt")}),
                ("Write", {"file_path": str(root / "tree" / "f2.txt")}),
                ("Glob", {"pattern": str(root / "tree" / "*.txt")}),
            ):
                gate.decide(ProposedCall(tool=tool, args=args))
        finally:
            sink.close()

        lines = [line for line in records.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert lines, "no records were written"
    median = sorted(len(line) for line in lines)[len(lines) // 2]
    assert low <= median <= high, (
        f"a record is {median:,} bytes and the README publishes {low:,} to {high:,}. "
        "Re-measure and update the cost table rather than leaving a number that flatters us."
    )


def _published_record_size() -> tuple[int, int]:
    """The range out of the README's cost table, so the test reads the claim rather than a copy."""
    import re

    text = (REPO / "README.md").read_text(encoding="utf-8")
    match = re.search("\\*\\*~([\\d.]+)\u2013([\\d.]+) KB per call\\*\\*", text)
    assert match, "the README's record-size claim has changed shape; update this test with it"
    return int(float(match.group(1)) * 1000), int(float(match.group(2)) * 1000)
