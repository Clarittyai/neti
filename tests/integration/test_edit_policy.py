"""Declaring a ceiling from the console, without wrecking the file it goes in.

Two things this must never do, in order of how much damage they cause:

1. **Land the ceiling on the wrong gate.** `/file_path` appears under four tools in the shipped
   example. An edit that matched the first one would produce a file that parses, loads, and gates
   the wrong thing — the worst failure available here, because everything downstream reports
   success.
2. **Lose the comments.** `examples/coding-agent.yaml` is more comment than configuration, and the
   comments are most of its value. A YAML round trip would write back a correct file nobody could
   read again, which is why this is a text splice.

Everything else is the `neti install` contract applied to a second file somebody owns: plan before
you write, back it up, be honest about replacing something that was already there.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from neti.config.policy import load_policy
from neti.insight.edit_policy import PolicyEditError, apply_ceiling, plan_ceiling

SHIPPED = Path("examples/coding-agent.yaml")


@pytest.fixture
def policy(tmp_path: Path) -> Path:
    """A copy of the real shipped example, because that is the file this has to survive — heavily
    commented, with a commented-out `bands:` block sitting right where a live one would go."""
    target = tmp_path / "neti.yaml"
    target.write_text(SHIPPED.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def bands_of(path: Path, tool: str, pointer: str) -> list[tuple[int, str]]:
    spec = load_policy(path).gate_specs(tool)[pointer]
    return sorted((b.above, b.verdict.name.lower()) for b in spec.bands)


# --------------------------------------------------------------------------- it lands where aimed


def test_the_ceiling_lands_on_the_gate_it_was_aimed_at(policy: Path) -> None:
    """`/file_path` is gated under Read, Edit and Write. Matching the first one would be silent."""
    plan = plan_ceiling(
        policy, tool="Write", pointer="/file_path", bands=[{"above": 3, "verdict": "block"}]
    )
    apply_ceiling(plan)

    assert bands_of(policy, "Write", "/file_path") == [(3, "block")]
    assert bands_of(policy, "Read", "/file_path") == []
    assert bands_of(policy, "Edit", "/file_path") == []


def test_an_existing_ceiling_is_replaced_rather_than_duplicated(policy: Path) -> None:
    """Appending a second `bands:` would give YAML a duplicate key — valid to the parser, and not
    what anybody meant. The replacement is announced, because overwriting a ceiling somebody
    committed is a different act from adding a first one."""
    apply_ceiling(
        plan_ceiling(
            policy, tool="Glob", pointer="/pattern", bands=[{"above": 10, "verdict": "block"}]
        )
    )
    second = plan_ceiling(
        policy, tool="Glob", pointer="/pattern", bands=[{"above": 99, "verdict": "confirm"}]
    )

    assert second.replaced
    assert any("replaces it" in w for w in second.warnings)

    apply_ceiling(second)
    assert bands_of(policy, "Glob", "/pattern") == [(99, "confirm")]


def test_a_gate_that_is_not_there_is_refused_rather_than_invented(policy: Path) -> None:
    with pytest.raises(PolicyEditError):
        plan_ceiling(
            policy, tool="NotATool", pointer="/x", bands=[{"above": 1, "verdict": "block"}]
        )
    with pytest.raises(PolicyEditError):
        plan_ceiling(policy, tool="Glob", pointer="/nope", bands=[{"above": 1, "verdict": "block"}])


# --------------------------------------------------------------------------- it keeps the file


def test_every_comment_survives(policy: Path) -> None:
    before = policy.read_text(encoding="utf-8").splitlines()
    apply_ceiling(
        plan_ceiling(
            policy, tool="Bash", pointer="/command", bands=[{"above": 2000, "verdict": "block"}]
        )
    )
    after = policy.read_text(encoding="utf-8").splitlines()

    comments = [line for line in before if line.strip().startswith("#")]
    assert comments, "the fixture is supposed to be a heavily commented file"
    assert comments == [line for line in after if line.strip().startswith("#")]


def test_nothing_outside_the_edited_gate_moves(policy: Path) -> None:
    """The whole reason this is a splice. Every other line is byte-identical."""
    before = policy.read_text(encoding="utf-8").splitlines()
    apply_ceiling(
        plan_ceiling(
            policy, tool="Bash", pointer="/command", bands=[{"above": 2000, "verdict": "block"}]
        )
    )
    after = policy.read_text(encoding="utf-8").splitlines()

    added = [line for line in after if line not in before]
    assert added == ["        bands:", "          - { above: 2000, verdict: block }"]
    assert [line for line in before if line not in after] == []


def test_the_previous_version_is_kept(policy: Path) -> None:
    original = policy.read_text(encoding="utf-8")
    backup = apply_ceiling(
        plan_ceiling(
            policy, tool="Glob", pointer="/pattern", bands=[{"above": 1, "verdict": "block"}]
        )
    )

    assert backup is not None and backup.exists()
    assert backup.read_text(encoding="utf-8") == original


def test_the_result_is_valid_yaml_and_a_loadable_policy(policy: Path) -> None:
    apply_ceiling(
        plan_ceiling(
            policy, tool="Grep", pointer="/path", bands=[{"above": 7, "verdict": "confirm"}]
        )
    )

    yaml.safe_load(policy.read_text(encoding="utf-8"))
    assert load_policy(policy).gate_specs("Grep")["/path"].has_ceiling


# --------------------------------------------------------------------------- it refuses nonsense


@pytest.mark.parametrize(
    "bands",
    [
        [],
        [{"above": "not a number", "verdict": "block"}],
        [{"above": -1, "verdict": "block"}],
        [{"above": 10, "verdict": "explode"}],
        [{"above": 10, "verdict": "block"}, {"above": 10, "verdict": "confirm"}],
    ],
)
def test_a_ceiling_that_cannot_mean_anything_never_reaches_the_file(
    policy: Path, bands: list[dict[str, object]]
) -> None:
    """A policy that will not load is not a rejected form, it is a disabled gate.

    `neti hook` exits 0 on a policy error — deliberately, because a crashed `PreToolUse` hook takes
    out every tool call in the session — and says why on stderr, where nothing reads it. So a bad
    band written here would run the next session entirely ungated. Rejecting it before the write is
    the difference.
    """
    before = policy.read_text(encoding="utf-8")
    with pytest.raises(PolicyEditError):
        plan_ceiling(policy, tool="Glob", pointer="/pattern", bands=bands)
    assert policy.read_text(encoding="utf-8") == before


def test_planning_writes_nothing(policy: Path) -> None:
    """The whole point of two calls. `neti install` shows the diff before touching settings.json,
    and a policy is that file twice over."""
    before = policy.read_text(encoding="utf-8")
    plan = plan_ceiling(
        policy, tool="Glob", pointer="/pattern", bands=[{"above": 4, "verdict": "block"}]
    )

    assert plan.changed and plan.diff()
    assert policy.read_text(encoding="utf-8") == before


def test_a_four_space_file_is_edited_in_its_own_style(tmp_path: Path) -> None:
    """The indentation is read from the file rather than imposed on it. Writing two spaces into a
    four-space document produces valid YAML that its author did not write."""
    target = tmp_path / "wide.yaml"
    target.write_text(
        "version: 1\n"
        "tools:\n"
        "    Glob:\n"
        "        gate:\n"
        "            /pattern:\n"
        "                resolver: fs.paths\n"
        "                on_unresolved: allow\n",
        encoding="utf-8",
    )
    apply_ceiling(
        plan_ceiling(
            target, tool="Glob", pointer="/pattern", bands=[{"above": 5, "verdict": "block"}]
        )
    )

    assert "                bands:\n" in target.read_text(encoding="utf-8")
    assert bands_of(target, "Glob", "/pattern") == [(5, "block")]


def test_an_inline_gate_stays_inline(policy: Path) -> None:
    """`Read`, `Edit` and `Write` are one-line flow mappings in the shipped example.

    A block `bands:` spliced under one is invalid YAML — `_verify` caught that and refused, which is
    the net working, but it left three of nine gates uneditable from the console. Flow in, flow out:
    somebody's formatting is not this module's to normalise.
    """
    apply_ceiling(
        plan_ceiling(
            policy, tool="Read", pointer="/file_path", bands=[{"above": 1, "verdict": "confirm"}]
        )
    )

    line = next(
        line
        for line in policy.read_text(encoding="utf-8").splitlines()
        if "/file_path:" in line and "confirm" in line
    )
    assert line.strip().startswith("/file_path: {") and line.rstrip().endswith("}")
    assert bands_of(policy, "Read", "/file_path") == [(1, "confirm")]
    # And only that one. The other two are the same string on adjacent lines.
    assert bands_of(policy, "Edit", "/file_path") == []
    assert bands_of(policy, "Write", "/file_path") == []


def test_replacing_a_ceiling_on_an_inline_gate_does_not_duplicate_the_key(policy: Path) -> None:
    apply_ceiling(
        plan_ceiling(
            policy, tool="Edit", pointer="/file_path", bands=[{"above": 2, "verdict": "block"}]
        )
    )
    second = plan_ceiling(
        policy, tool="Edit", pointer="/file_path", bands=[{"above": 9, "verdict": "confirm"}]
    )
    assert second.replaced
    apply_ceiling(second)

    line = next(
        line
        for line in policy.read_text(encoding="utf-8").splitlines()
        if "/file_path:" in line and "confirm" in line
    )
    assert line.count("bands:") == 1
    assert bands_of(policy, "Edit", "/file_path") == [(9, "confirm")]
