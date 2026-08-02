"""Invariant 9: every change to what the product *says* is a diff a human reads.

Four of the defects found by using this product were the CLI describing its own state falsely, and
none of them is expressible as an assertion anybody would think to write in advance:

- `neti init` announced "No MCP servers found in any client config on this machine" to an operator
  who had just finished gating all of theirs.
- `neti propose` printed `p95=25` while the p95 was 41,203.
- `neti init` gated nothing, then told the reader to run `neti inventory`, which had nothing to
  report — every line true, the sequence useless.
- A wrapped server's startup banner interleaved with a scan's progress and made it look broken.

The suite was green through all four, because it tested return values and never once tested the
words. This file does. It is not clever — it runs each command in a known state and compares the
output to a checked-in transcript. The value is that the wording is now under version control: a
change to what an operator is told cannot land without somebody looking at it.

Volatile parts — paths, digests, ids, timings — are normalised, so a diff means the product started
saying something different and nothing else. `UPDATE_GOLDEN=1 uv run pytest tests/golden` rewrites
them; the diff is then the review.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from neti.cli import app
from tests.golden.conftest import break_chain, write_records

GOLDEN = Path(__file__).resolve().parent / "transcripts"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"

_NOISE: list[tuple[re.Pattern[str], str]] = [
    # Order matters, and getting it wrong is silent. UUIDs first: the digest pattern otherwise eats
    # a uuid's final 12-hex segment, leaving the four varying segments in front of it — transcripts
    # that looked normalised and failed on every run.
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<id>"),
    (re.compile(r"\b[0-9a-f]{12,64}\b"), "<digest>"),
    # `neti report` cites a decision by an 8-char prefix, which is shorter than either pattern
    # above. Scoped to the parenthesised form so it cannot swallow ordinary content.
    (re.compile(r"\(([0-9a-f]{8})\)"), "(<id>)"),
    (
        re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?"),
        "<time>",
    ),
    (re.compile(r"\b\d+(?:\.\d+)?\s?ms\b"), "<ms>"),
    (re.compile(r"/(?:private/)?(?:tmp|var)/[^\s'\"),]+"), "<path>"),
    (re.compile(r"/Users/[^\s'\"),]+"), "<path>"),
    # Rich wraps to the terminal width, which differs between a developer's shell and CI.
    (re.compile(r"[ \t]+$", re.M), ""),
]


def normalise(text: str) -> str:
    for pattern, replacement in _NOISE:
        text = pattern.sub(replacement, text)
    return text.strip() + "\n"


def check(
    name: str,
    args: list[str],
    *,
    setup: Callable[[Path], None] | None = None,
    workspace: Path,
    stdin: str | None = None,
) -> None:
    """Run one command in a known state and hold its output to the checked-in transcript."""
    if setup is not None:
        setup(workspace)

    result = CliRunner().invoke(app, args, input=stdin, catch_exceptions=False)
    actual = normalise(f"$ neti {' '.join(args)}\n[exit {result.exit_code}]\n{result.output}")

    path = GOLDEN / f"{name}.txt"
    if UPDATE or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        if not UPDATE:
            pytest.fail(f"wrote a new transcript {path.name} — review it and commit it")
        return

    expected = path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"`neti {' '.join(args)}` says something different now.\n"
        f"If the new wording is better, rerun with UPDATE_GOLDEN=1 and review the diff.\n"
        f"--- {path.name} (checked in)\n{expected}\n+++ actual\n{actual}"
    )


# ---------------------------------------------------------------------------- init


# `--out` to a fresh path on purpose. The workspace ships a `neti.yaml`, so a bare `neti init`
# stops at "already exists" — which is how the first version of these two transcripts came to pin
# that error instead of the messages they exist to protect. A snapshot of the wrong state is worse
# than no snapshot, because it reads as coverage.


def test_init_finds_nothing(workspace: Path) -> None:
    check("init_no_servers", ["init", "--out", "gen.yaml"], workspace=workspace)


def test_init_when_everything_is_already_gated(workspace: Path) -> None:
    def setup(ws: Path) -> None:
        (ws / ".mcp.json").write_text(
            '{"mcpServers": {"entra": {"command": "neti", "args": ["gate", "--stdio"]}}}'
        )

    check("init_all_gated", ["init", "--out", "gen.yaml"], setup=setup, workspace=workspace)


# ---------------------------------------------------------------------------- inventory


def test_inventory_with_the_example_policy(workspace: Path) -> None:
    check("inventory_rows", ["inventory", "--demo", "-c", "neti.yaml"], workspace=workspace)


def test_inventory_with_nothing_gated(workspace: Path) -> None:
    def setup(ws: Path) -> None:
        (ws / "empty.yaml").write_text("version: 1\nmode: observe\ntools: {}\n")

    check(
        "inventory_nothing_gated",
        ["inventory", "--demo", "-c", "empty.yaml"],
        setup=setup,
        workspace=workspace,
    )


# ---------------------------------------------------------------------------- report / propose


def test_report_on_an_empty_corpus(workspace: Path) -> None:
    def setup(ws: Path) -> None:
        (ws / "none.ndjson").write_text("")

    check("report_empty", ["report", "-r", "none.ndjson"], setup=setup, workspace=workspace)


def test_report_on_a_bimodal_corpus(workspace: Path) -> None:
    def setup(ws: Path) -> None:
        write_records(ws / "d.ndjson", [25] * 32 + [500] * 4 + [41_203] * 4)

    check("report_bimodal", ["report", "-r", "d.ndjson"], setup=setup, workspace=workspace)


def test_propose_below_the_threshold(workspace: Path) -> None:
    def setup(ws: Path) -> None:
        write_records(ws / "d.ndjson", [25] * 5)

    check("propose_too_few", ["propose", "-r", "d.ndjson"], setup=setup, workspace=workspace)


def test_propose_on_a_bimodal_corpus(workspace: Path) -> None:
    """The one that proposed `block above 500,000`. Its wording is now under review."""

    def setup(ws: Path) -> None:
        write_records(ws / "d.ndjson", [25] * 32 + [500] * 4 + [41_203] * 4)

    check("propose_bimodal", ["propose", "-r", "d.ndjson"], setup=setup, workspace=workspace)


def test_propose_on_a_uniform_corpus(workspace: Path) -> None:
    def setup(ws: Path) -> None:
        write_records(ws / "d.ndjson", [3] * 60)

    check("propose_uniform", ["propose", "-r", "d.ndjson"], setup=setup, workspace=workspace)


def test_report_rejects_an_unreadable_window(workspace: Path) -> None:
    def setup(ws: Path) -> None:
        write_records(ws / "d.ndjson", [25] * 3)

    check(
        "report_bad_since",
        ["report", "--since", "lastweek", "-r", "d.ndjson"],
        setup=setup,
        workspace=workspace,
    )


# ---------------------------------------------------------------------------- verify


def test_verify_an_intact_chain(workspace: Path) -> None:
    def setup(ws: Path) -> None:
        write_records(ws / "d.ndjson", [25] * 3)

    check("verify_intact", ["verify", "-r", "d.ndjson"], setup=setup, workspace=workspace)


def test_verify_a_tampered_chain(workspace: Path) -> None:
    def setup(ws: Path) -> None:
        break_chain(write_records(ws / "d.ndjson", [25] * 3))

    check("verify_broken", ["verify", "-r", "d.ndjson"], setup=setup, workspace=workspace)


def test_verify_a_missing_file(workspace: Path) -> None:
    check("verify_missing", ["verify", "-r", "nope.ndjson"], workspace=workspace)


# ---------------------------------------------------------------------------- policy errors


def test_a_resolver_that_does_not_exist(workspace: Path) -> None:
    def setup(ws: Path) -> None:
        (ws / "typo.yaml").write_text(
            "version: 1\nmode: enforce\ntools:\n  send_email:\n    gate:\n      /to:\n"
            "        resolver: entra.principal\n        bands: [{ above: 10, verdict: block }]\n"
        )

    check(
        "policy_unknown_resolver",
        ["inventory", "--demo", "-c", "typo.yaml"],
        setup=setup,
        workspace=workspace,
    )


def test_a_breakdown_band_nothing_emits(workspace: Path) -> None:
    """The guard for the bug that was live in the shipped example."""

    def setup(ws: Path) -> None:
        (ws / "dead.yaml").write_text(
            "version: 1\nmode: enforce\ntools:\n  send_email:\n    gate:\n      /to:\n"
            "        resolver: entra.principals\n        bands: [{ above: 10, verdict: block }]\n"
            "        breakdown_bands:\n          guest:\n"
            "            - { above: 5, verdict: block }\n"
        )

    check(
        "policy_dead_breakdown",
        ["gate", "--stdio", "--demo", "-c", "dead.yaml", "--", "true"],
        setup=setup,
        workspace=workspace,
        stdin="",
    )


def test_malformed_yaml(workspace: Path) -> None:
    def setup(ws: Path) -> None:
        (ws / "bad.yaml").write_text("version: 1\n  mode: [unclosed\n")

    check(
        "policy_malformed",
        ["inventory", "--demo", "-c", "bad.yaml"],
        setup=setup,
        workspace=workspace,
    )


# ---------------------------------------------------------------------------- hook


@pytest.mark.parametrize(
    ("name", "tool", "args"),
    [
        ("hook_block", "remove_group_members", '{"group": "g-eng-all"}'),
        ("hook_confirm", "send_email", '{"to": "g-dept"}'),
        ("hook_silent_pass", "send_email", '{"to": "g-team"}'),
        ("hook_ungated_tool", "Bash", '{"command": "ls"}'),
    ],
)
def test_hook_says_the_right_thing(workspace: Path, name: str, tool: str, args: str) -> None:
    """A pass must say *nothing at all* — emitting `allow` would bypass the operator's own
    permission rules, which is a security tool widening what an agent may do."""
    check(
        name,
        ["hook", "--demo", "--mode", "enforce", "-c", "neti.yaml", "-r", "h.ndjson"],
        workspace=workspace,
        stdin=f'{{"hook_event_name": "PreToolUse", "tool_name": "{tool}", "tool_input": {args}}}',
    )


# ---------------------------------------------------------------------------- demo --here
#
# The demo is the output most likely to be read by a stranger and least likely to be re-read by us,
# which is exactly the combination the golden mechanism exists for. Its wording carries claims —
# what is measured, what is borrowed, what a number means — and a claim that drifts is a claim
# nobody notices drifting.


def _fixture_repo(ws: Path) -> Path:
    """A tree with a known file count, so the transcript is a fixed number rather than whatever the
    machine happens to hold."""
    repo = ws / "subject"
    for name, count in (("src", 8), ("vendor", 24)):
        (repo / name).mkdir(parents=True)
        for i in range(count):
            (repo / name / f"f{i}.py").write_text("x")
    return repo


def test_demo_here_with_no_traffic(workspace: Path) -> None:
    """The first run every evaluator gets.

    A measurement, and a plain statement of what is missing.
    """
    repo = _fixture_repo(workspace)
    policy = Path(__file__).resolve().parents[2] / "examples" / "coding-agent.yaml"

    check(
        "demo_here_no_traffic",
        ["demo", "--here", "--repo", str(repo), "-c", str(policy)],
        workspace=workspace,
    )


def test_demo_here_with_traffic(workspace: Path) -> None:
    """All six acts. The act-3 caveat and the closing disclaimer are the lines that matter most."""
    repo = _fixture_repo(workspace)
    policy = Path(__file__).resolve().parents[2] / "examples" / "coding-agent.yaml"

    def setup(ws: Path) -> None:
        from neti.eval.corpus import capture, write_corpus
        from neti.preflight import Preflight
        from neti.store.jsonl import read_records

        records = ws / "captured.ndjson"
        pf = Preflight.demo(policy, mode="observe", records=records)
        for i in range(40):
            pf.check("Grep", {"path": str(repo / ("vendor" if i % 10 == 0 else "src"))})
        write_corpus(capture(read_records(records), repo, source="a fixture"), ws / "corpus.jsonl")

    check(
        "demo_here_full",
        [
            "demo",
            "--here",
            "--repo",
            str(repo),
            "-c",
            str(policy),
            "--corpus",
            str(workspace / "corpus.jsonl"),
        ],
        setup=setup,
        workspace=workspace,
    )
