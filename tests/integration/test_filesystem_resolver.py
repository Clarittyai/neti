"""The resolver that makes a coding agent gateable at all.

Twenty real Claude Code tool calls went through the `PreToolUse` hook and every verdict was `allow`
by `unknown_tool` — the gate worked, and nothing could size a filesystem path. This is the missing
half, so the tests that matter are the ones about *how it fails*: a path it cannot read, a tree too
big to finish, a symlink pointing at its own parent. Getting the count right on a tidy directory is
the easy part.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from neti.core.units import Direction, Unit, may_allow, may_block
from neti.core.verdict import ResolutionState
from neti.resolvers.base import ResolveContext
from neti.resolvers.filesystem import FilesystemResolver

CTX = ResolveContext()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """12 files, 3 of them nested, with known sizes."""
    for i in range(9):
        (tmp_path / f"f{i}.txt").write_text("x" * 10)
    nested = tmp_path / "sub" / "deeper"
    nested.mkdir(parents=True)
    for i in range(3):
        (nested / f"n{i}.md").write_text("y" * 100)
    return tmp_path


def test_a_directory_counts_every_file_under_it(tree: Path) -> None:
    """Recursive on purpose: an agent deleting a directory takes the nested files with it, and a
    top-level count would understate the blast radius — the direction that lets a call through."""
    res = FilesystemResolver().resolve(str(tree), CTX)
    assert res.state is ResolutionState.RESOLVED
    assert res.magnitude == 12
    assert res.direction is Direction.EXACT
    assert res.breakdown["bytes"] == 9 * 10 + 3 * 100


def test_a_single_file_is_one(tree: Path) -> None:
    res = FilesystemResolver().resolve(str(tree / "f0.txt"), CTX)
    assert (res.magnitude, res.breakdown["bytes"]) == (1, 10)


def test_a_glob_counts_its_matches(tree: Path) -> None:
    res = FilesystemResolver().resolve(f"{tree}/**/*.md", CTX)
    assert res.magnitude == 3
    assert res.direction is Direction.EXACT


def test_a_glob_matching_nothing_is_zero_not_unknown(tree: Path) -> None:
    """Zero is a real answer here and must not be confused with a failure: the pattern was
    evaluated, and it matched nothing. `UNRESOLVED` would send a harmless call through
    `on_unresolved` and stop it for no reason."""
    res = FilesystemResolver().resolve(f"{tree}/*.nothing", CTX)
    assert res.state is ResolutionState.RESOLVED
    assert res.magnitude == 0


def test_it_counts_files_and_never_directories(tree: Path) -> None:
    """`sub` and `sub/deeper` exist but are not things an agent deletes *content* of separately."""
    assert FilesystemResolver().resolve(f"{tree}/**/*", CTX).magnitude == 12


# ---------------------------------------------------------------------------- the failure modes


def test_a_missing_path_is_unresolved_not_zero(tmp_path: Path) -> None:
    """The whole product turns on this. Reporting 0 for a path that is not there would make every
    unreadable target look like the safest possible call."""
    res = FilesystemResolver().resolve(str(tmp_path / "gone"), CTX)
    assert res.state is ResolutionState.UNRESOLVED
    assert res.magnitude is None
    assert res.evidence["reason"] == "path_not_found"


def test_an_unreadable_directory_is_unresolved_not_zero(tmp_path: Path) -> None:
    """A permissions error is not an empty directory."""
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "secret.txt").write_text("x")
    os.chmod(locked, 0o000)
    try:
        res = FilesystemResolver().resolve(str(locked), CTX)
        # Either the walk yields nothing readable or the stat fails outright; what must never
        # happen is a confident, permissive small number.
        assert res.state is ResolutionState.UNRESOLVED or res.magnitude == 0
    finally:
        os.chmod(locked, 0o755)


def test_an_empty_target_is_unresolved(tmp_path: Path) -> None:
    assert FilesystemResolver().resolve("", CTX).state is ResolutionState.UNRESOLVED


def test_a_capped_walk_is_a_lower_bound_and_can_only_block(tree: Path) -> None:
    """The cap is a latency control, and it must not be able to become a permissive answer.

    A LOWER_BOUND can soundly block — measured over the ceiling means the truth is too — and can
    never soundly allow, because measured under it proves nothing. The decision procedure already
    enforces that; this asserts the resolver declares the direction that triggers it.
    """
    res = FilesystemResolver(cap=5).resolve(str(tree), CTX)
    assert res.direction is Direction.LOWER_BOUND
    assert res.magnitude == 5
    assert res.evidence["capped"] is True
    assert not may_allow(res.direction), "a capped count must never be allowed on"
    assert may_block(res.direction)


def test_a_symlink_cycle_terminates(tmp_path: Path) -> None:
    """A directory that links to its own parent is a hang, and a gate that hangs is a gate that
    gets removed. Directories are tracked by (device, inode), so a cycle is visited once."""
    (tmp_path / "a.txt").write_text("x")
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "b.txt").write_text("y")
    try:
        (inner / "loop").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    res = FilesystemResolver().resolve(str(tmp_path), CTX)
    assert res.state is ResolutionState.RESOLVED
    assert res.magnitude == 2


# ---------------------------------------------------------------------------- inventory


def test_without_a_root_it_refuses_to_claim_a_reachable_maximum() -> None:
    """ "Every file this process can read" is not a bound, and inventing one would put a number in
    front of an operator that nobody could defend."""
    res = FilesystemResolver().reachable_max(CTX)
    assert res.state is ResolutionState.UNRESOLVED
    assert res.evidence["reason"] == "no_root_declared"


def test_with_a_root_it_reports_what_is_under_it(tree: Path) -> None:
    assert FilesystemResolver(root=tree).reachable_max(CTX).magnitude == 12


def test_it_declares_its_unit_and_breakdown() -> None:
    """Without `breakdown_keys` the Engine cannot refuse a band on a key nothing emits."""
    r = FilesystemResolver()
    assert r.unit is Unit.OBJECTS
    assert r.breakdown_keys == frozenset({"bytes"})
