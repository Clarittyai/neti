"""`shell.paths`: what it claims, and — mostly — what it refuses to claim.

The negative table is longer than the positive one on purpose. A shell command is arbitrary code,
and this resolver only earns its place by being trustworthy about the small set it recognises. The
two failure modes are not symmetric:

  - Declining a destructive command loses coverage; the declared `on_unresolved` still decides.
  - Returning a count that is too small lets a large deletion through under a ceiling.

Only the second is unsound, so every ambiguity has to resolve toward the first, and these are the
tests that hold it there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neti.resolvers.base import ResolveContext
from neti.resolvers.shell import ShellPathsResolver, targets_of

UNDERSTOOD = [
    ("rm -rf build", ("build",)),
    ("rm file.txt", ("file.txt",)),
    ("sudo rm -rf /tmp/a /tmp/b", ("/tmp/a", "/tmp/b")),
    ("rm -- -weird-name", ("-weird-name",)),
    ("/bin/rm -r dist", ("dist",)),
    ("find . -name '*.log' -delete", (".",)),
    ("find src tests -type f -delete", ("src", "tests")),
    ("git clean -fd", (".",)),
    ("git clean -fdx web", ("web",)),
    ("git checkout -- src/", ("src/",)),
    ("shred secrets.env", ("secrets.env",)),
]

DECLINED = [
    # Anything whose real behaviour is not on the line being read.
    ("cat list.txt | xargs rm", "compound_command"),
    ("rm -rf a && rm -rf b", "compound_command"),
    ("rm -rf $(cat target)", "compound_command"),
    ("rm -rf `cat target`", "compound_command"),
    ("rm -rf a; rm -rf b", "compound_command"),
    # A variable is not a path. The shell expands it to something this cannot see.
    ("rm -rf $TARGET", "target_contains_a_shell_variable"),
    # Commands that delete but are not readable from their name.
    ("python cleanup.py", "unrecognised_command:python"),
    ("make clean", "unrecognised_command:make"),
    ("./scripts/wipe.sh", "unrecognised_command:wipe.sh"),
    ("npm test", "unrecognised_command:npm"),
    # `find` that only reads.
    ("find . -type f", "find_without_delete"),
    ("find . -name '*.py'", "find_without_delete"),
    # `git` that touches no working-tree file.
    ("git status", "git_status_is_not_destructive_to_the_worktree"),
    ("git clean -n", "git_clean_is_not_destructive_to_the_worktree"),
    ("git checkout main", "git_checkout_is_not_destructive_to_the_worktree"),
    ("", "empty_command"),
    ("rm -rf", "rm_like_without_a_target"),
]


@pytest.mark.parametrize("command, paths", UNDERSTOOD, ids=lambda v: str(v)[:40])
def test_the_commands_it_reads(command: str, paths: tuple[str, ...]) -> None:
    read = targets_of(command)
    assert read.understood, f"{command!r} was declined: {read.reason}"
    assert read.paths == paths


@pytest.mark.parametrize("command, reason", DECLINED, ids=lambda v: str(v)[:40])
def test_the_commands_it_refuses_to_read(command: str, reason: str) -> None:
    """Each of these could delete something. None of them can be sized from the text alone."""
    read = targets_of(command)
    assert not read.understood, f"{command!r} was claimed as {read.paths}, which it cannot know"
    assert read.reason == reason


def test_a_find_filter_never_shrinks_the_count(tmp_path: Path) -> None:
    """`find . -name '*.log' -delete` is sized as the whole of `.`, not as the matching files.

    Honouring the filter would produce a *smaller* number than the search root, and a number that is
    too small is the one error this must never make. Over-counting is sound: a bound can prove
    something is too big, never that it is small enough.
    """
    for index in range(12):
        (tmp_path / f"f{index}.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "one.log").write_text("x\n", encoding="utf-8")

    resolver = ShellPathsResolver(root=str(tmp_path))
    answer = resolver.resolve(f"find {tmp_path} -name '*.log' -delete", ResolveContext())

    assert answer.magnitude == 13, "the count shrank to the filter's matches"


def test_one_unreadable_target_fails_the_whole_command(tmp_path: Path) -> None:
    """`rm -rf real/ /does/not/exist` must not report only what it could count.

    Summing the readable half would answer with a number smaller than the command's blast radius,
    which is precisely the unsound direction.
    """
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    resolver = ShellPathsResolver(root=str(tmp_path))

    answer = resolver.resolve(f"rm -rf {tmp_path} /no/such/path/here", ResolveContext())
    assert answer.magnitude is None, "it counted a subset of a command it could not fully read"


def test_the_targets_are_summed_not_maxed(tmp_path: Path) -> None:
    """A command with three arguments deletes all three."""
    for name in ("a", "b"):
        directory = tmp_path / name
        directory.mkdir()
        for index in range(5):
            (directory / f"{index}.txt").write_text("x\n", encoding="utf-8")

    resolver = ShellPathsResolver(root=str(tmp_path))
    answer = resolver.resolve(f"rm -rf {tmp_path}/a {tmp_path}/b", ResolveContext())
    assert answer.magnitude == 10
