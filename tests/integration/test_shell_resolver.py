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
from neti.resolvers.shell import ShellPathsResolver, destructive_signal, targets_of

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


# ------------------------------------------------ forms an agent reaches for that were invisible


@pytest.mark.parametrize(
    ("command", "form"),
    [
        # Overwriting a file without ever calling `rm` — the most common shape by far.
        ("sed -i 's/a/b/' app.ts", "sed_in_place"),
        ("sed -i.bak 's/a/b/' app.ts", "sed_in_place"),
        ("sed -ni 's/a/b/p' app.ts", "sed_in_place"),
        ("pytest -q | tee results.txt", "tee_truncate"),
        ("rsync -a --delete src/ dst/", "rsync_delete"),
        ("rsync -a --delete-after src/ dst/", "rsync_delete"),
        # Work nothing gets back.
        ("git branch -D feature/x", "git_branch_force_delete"),
        ("git stash drop", "git_stash_drop"),
        ("git stash clear", "git_stash_clear"),
        # Data that does not live in the repository.
        ("docker volume rm pgdata", "docker_remove"),
        ("docker rmi build-cache", "docker_remove"),
        ("docker system prune -f", "docker_prune"),
        ("kubectl delete deployment api", "kubectl_delete"),
        ("terraform destroy", "terraform_destroy"),
        ("terraform apply -destroy -auto-approve", "terraform_destroy"),
        ("aws s3 rm s3://bucket/prefix --recursive", "aws_s3_rm"),
        ("aws s3 rb s3://bucket", "aws_s3_rb"),
        ("gsutil rm -r gs://bucket/prefix", "gsutil_rm"),
        # Still seen through a wrapper shell, like every other form.
        ("bash -c 'git stash clear'", "bash_c:git_stash_clear"),
    ],
)
def test_a_destructive_form_is_flagged_even_though_it_cannot_be_sized(
    command: str, form: str
) -> None:
    """Every form here widens the *flag* half only. None of them teaches sizing a number.

    That asymmetry is the design: flagging costs a line in `neti report`, and mis-sizing lets a
    deletion through under a ceiling. So this list may grow much faster than the sizing table.
    """
    signal = destructive_signal(command)
    assert signal is not None, f"{command!r} destroys something and was not flagged"
    assert signal.form == form


@pytest.mark.parametrize(
    "command",
    [
        # The non-destructive spelling of each form above. These carry more weight than the
        # positives: a recogniser that fired on both teaches an operator to stop reading the flag,
        # which is why `--force-with-lease` is deliberately absent from the `git push` check too.
        "sed 's/a/b/' app.ts",
        "sed -n '1,5p' app.ts",
        "cat access.log | tee -a audit.log",
        "cat access.log | tee --append audit.log",
        "pytest -q | tee",
        "rsync -av src/ dst/",
        "git branch -d merged-feature",
        "git branch --list",
        "git stash list",
        "git stash show",
        "docker ps -a",
        "docker build -t app .",
        "kubectl get deployments",
        "kubectl describe pod api",
        "terraform plan",
        "terraform apply",
        "aws s3 ls s3://bucket",
        "aws s3 cp file s3://bucket/k",
        "gsutil ls gs://bucket",
        # Mentions a destructive verb without being in command position.
        "grep -rn 'sed -i' src/",
        "echo 'terraform destroy is dangerous'",
        # Changes permissions, destroys no data. Deliberately not a signal — folding "broke the
        # permissions" into "deleted the files" makes the signal mean less.
        "chmod -R 777 .",
        "chown -R me:me .",
        # Ordinary work, which has to stay silent or the gate gets removed on a Friday.
        "npm test",
        "pytest -q",
        "git status",
    ],
)
def test_a_command_that_destroys_nothing_stays_silent(command: str) -> None:
    assert destructive_signal(command) is None, f"{command!r} was flagged and destroys nothing"


# ------------------------------------------------------- the separator that was read as an escape


def test_a_windows_path_survives_the_split(monkeypatch: pytest.MonkeyPatch) -> None:
    """**`shell.paths` could not size any deletion on Windows.**

    `shlex.split` defaults to POSIX mode, where `\\` is an escape character. So

        rm -rf C:\\Users\\me\\build

    split to the single token `C:Usersmebuild` — a path that does not exist, so `fs.paths` could not
    size it, so the call resolved UNRESOLVED, so the shipped policy's `on_unresolved: allow` let it
    through. Not sized wrongly: not seen at all, while reporting the same "not a recognised
    destructive command" note that `npm test` gets. The gate looked identical and gated nothing.

    Forced here rather than left to the Windows CI job, which is the whole point of the test. This
    was invisible for two compounding reasons — development happens on macOS, where the same
    string contains no backslashes, and the Windows job has never been green — so a test that only
    ran the branch on Windows would have stayed exactly as invisible as the bug.
    """
    monkeypatch.setattr("neti.resolvers.shell._POSIX", False)
    windows = r"C:\Users\runneradmin\AppData\Local\Temp\pytest-0\world\tree"

    assert targets_of(rf"rm -rf {windows}").paths == (windows,)
    assert targets_of(rf'rm -rf "{windows}"').paths == (windows,), "quotes come off in either mode"


def test_posix_escapes_still_mean_what_they_mean(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half. A backslash on a POSIX shell *is* an escape, and reading it as a separator
    would split one path into two — the mirror of the bug above, in the direction that under-counts
    a deletion rather than missing it."""
    monkeypatch.setattr("neti.resolvers.shell._POSIX", True)

    assert targets_of(r"rm -rf /tmp/a\ b").paths == ("/tmp/a b",)
    assert targets_of("rm -rf '/tmp/a b'").paths == ("/tmp/a b",)


def test_the_split_mode_follows_the_platform() -> None:
    """A backslash is an escape in `sh` and a separator in `cmd`, and no single reading is right in
    both. Asserted so that flipping the default is a deliberate act rather than a refactor."""
    import os

    from neti.resolvers.shell import _POSIX

    assert _POSIX is (os.name != "nt")
