"""`shell.paths` — how many files would this shell command destroy, before it runs.

**This is the level a coding agent actually acts at.** The shipped policy gates `Glob`, `Grep`,
`Read`, `Edit`, `Write` and three directory tools — and an agent that wants to delete something does
not reach for `Write`, it reaches for `Bash`. `rm -rf`, `find . -delete`, `git clean -fd`,
`git checkout -- .`: every one of them fell through `unknown_tool: allow` and was invisible.

So this reads the command, finds the paths it would touch, and hands them to `FilesystemResolver` —
the same counter, the same walk cap, the same `LOWER_BOUND` semantics as every other filesystem
gate. Nothing here counts anything itself.

**It declines far more often than it claims, and that is the design.** A shell command is arbitrary
code. This recognises a small, explicit set of destructive forms and answers UNRESOLVED for
everything else — a pipeline, a subshell, a `$(…)`, an unknown binary, a wrapper script, a flag it
has not seen. The declared `on_unresolved` then decides, which is the operator's call rather than
this parser's guess.

**A parser that over-claims is worse than no parser**, because a wrong number is a number somebody
will band against. The two failures are not symmetric:

  - Answering UNRESOLVED for a destructive command loses coverage. The policy still decides.
  - Answering a number that is too small lets a large deletion through under a ceiling.

Only the second is unsound, so every ambiguity resolves toward the first. The negative cases in
`tests/integration/test_shell_resolver.py` carry more weight than the positive ones for exactly this
reason.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import ClassVar

from neti.core.types import Resolution
from neti.core.units import Direction, Unit
from neti.resolvers.base import ResolveContext
from neti.resolvers.filesystem import DEFAULT_CAP, FilesystemResolver

__all__ = ["ShellPathsResolver", "targets_of"]

# Anything that makes a command line stop being a single, readable invocation. Seeing any of these
# is enough to decline: the parse below reasons about one command with one argument list, and a
# pipeline or a substitution means the thing that actually runs is not the thing being read.
_COMPOUND = ("|", ";", "&&", "||", "$(", "`", ">", ">>", "\n")

# The verbs this understands. Everything else — including `python`, `node`, `make`, a shell script,
# or a binary nobody here has heard of — is UNRESOLVED, because what it deletes is unknowable from
# its name.
_DESTRUCTIVE = frozenset({"rm", "shred", "truncate", "unlink"})

# Flags that take a value, so the value is not mistaken for a path.
_VALUED_FLAGS = frozenset({"--exclude", "--include", "-S", "--size", "-n", "--newer"})


@dataclass(frozen=True)
class Targets:
    """What a command was read as touching, and whether it was read at all."""

    paths: tuple[str, ...] = ()
    understood: bool = False
    reason: str = "not_a_recognised_destructive_command"


def _is_flag(token: str) -> bool:
    return token.startswith("-") and token != "-"


def targets_of(command: str) -> Targets:
    """The filesystem targets a command would destroy, or a refusal to say.

    Split out from the resolver so the parse can be tested as a table of strings without a
    filesystem anywhere near it — which is what makes the negative cases cheap enough to write a lot
    of.
    """
    text = (command or "").strip()
    if not text:
        return Targets(reason="empty_command")

    if any(marker in text for marker in _COMPOUND):
        # `cat list.txt | xargs rm` deletes whatever that file says. Reading the left-hand side
        # tells you nothing about the right, so this does not try.
        return Targets(reason="compound_command")

    try:
        tokens = shlex.split(text)
    except ValueError:
        return Targets(reason="unparseable_quoting")
    if not tokens:
        return Targets(reason="empty_command")

    # `sudo rm -rf /` is still `rm -rf /`. Peeling the prefix is safe: it changes who runs
    # the command, not what it touches.
    while tokens and tokens[0] in {"sudo", "command", "env", "nohup", "time"}:
        tokens = tokens[1:]
    if not tokens:
        return Targets(reason="empty_command")

    verb, rest = tokens[0].rsplit("/", 1)[-1], tokens[1:]

    if verb in _DESTRUCTIVE:
        return _positional(rest, "rm_like")
    if verb == "find":
        return _find(rest)
    if verb == "git":
        return _git(rest)
    return Targets(reason=f"unrecognised_command:{verb}")


def _positional(tokens: list[str], why: str) -> Targets:
    """Every non-flag argument, which for `rm` and friends is the target list."""
    paths: list[str] = []
    skip = False
    positional_only = False
    for token in tokens:
        if skip:
            skip = False
            continue
        if positional_only:
            # After `--` everything is a path, which is the entire point of `--`: it is how you
            # delete a file whose name begins with a dash. Treating `-weird-name` as a flag here
            # would drop a real target and shrink the count.
            paths.append(token)
            continue
        if token == "--":
            positional_only = True
            continue
        if token in _VALUED_FLAGS:
            skip = True
            continue
        if _is_flag(token):
            continue
        paths.append(token)
    if not paths:
        return Targets(reason=f"{why}_without_a_target")
    return _checked(tuple(paths), why)


def _checked(paths: tuple[str, ...], why: str) -> Targets:
    """Refuse a target this cannot read literally.

    `rm -rf $TARGET` reads as the literal string `$TARGET`, and the shell will expand it to
    something else entirely. It happens that a path called `$TARGET` does not exist, so the
    filesystem resolver would answer UNRESOLVED and the command would be declined anyway — but
    relying on that is relying on an accident. A variable is stated as unknowable here, by name.
    """
    for path in paths:
        if "$" in path or "~" in path[1:]:
            return Targets(reason="target_contains_a_shell_variable")
    return Targets(paths=paths, understood=True, reason=why)


def _find(tokens: list[str]) -> Targets:
    """`find <roots> … -delete` / `-exec rm`.

    A `find` that only prints is not destructive, so this claims nothing unless the expression
    actually removes something. Filters like `-name '*.log'` are deliberately ignored rather than
    applied: honouring them would make the count *smaller* than the search root, and a count that is
    too small is the one error this must never make. Counting the root over-counts, which is sound —
    a bound can prove something is too big.
    """
    deletes = "-delete" in tokens or (
        "-exec" in tokens and any(t.rsplit("/", 1)[-1] in _DESTRUCTIVE for t in tokens)
    )
    if not deletes:
        return Targets(reason="find_without_delete")

    roots: list[str] = []
    for token in tokens:
        if _is_flag(token):
            break  # the expression starts here; everything before it is a search root
        roots.append(token)
    if not roots:
        return Targets(reason="find_without_a_root")
    return _checked(tuple(roots), "find_delete")


def _git(tokens: list[str]) -> Targets:
    """The two git commands that delete working-tree files without asking."""
    if not tokens:
        return Targets(reason="git_without_a_subcommand")
    sub, rest = tokens[0], tokens[1:]

    if sub == "clean" and any(f.startswith("-") and "f" in f for f in rest):
        paths = [t for t in rest if not _is_flag(t)]
        return _checked(tuple(paths) or (".",), "git_clean")

    if sub in {"checkout", "restore"} and "--" in rest:
        paths = rest[rest.index("--") + 1 :]
        if paths:
            return _checked(tuple(paths), f"git_{sub}")

    return Targets(reason=f"git_{sub}_is_not_destructive_to_the_worktree")


@dataclass(frozen=True)
class ShellPathsResolver:
    """Sizes the filesystem blast radius of a shell command.

    Delegates every count to `FilesystemResolver`, so a glob here behaves exactly as the same glob
    behaves under `Glob` — including the walk cap and the `LOWER_BOUND` it produces past it.
    """

    root: str | None = None
    cap: int = DEFAULT_CAP

    unit: ClassVar[Unit] = Unit.OBJECTS
    breakdown_keys: ClassVar[frozenset[str]] = frozenset({"bytes"})

    _fs: FilesystemResolver = field(init=False, repr=False, compare=False, default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        from pathlib import Path

        object.__setattr__(
            self,
            "_fs",
            FilesystemResolver(root=Path(self.root) if self.root else None, cap=self.cap),
        )

    def resolve(self, target: str, ctx: ResolveContext) -> Resolution:
        read = targets_of(target)
        if not read.understood:
            return Resolution.unresolved(
                self.unit,
                reason=read.reason,
                evidence={
                    "command": target[:200],
                    "note": "not a recognised destructive command; on_unresolved decides",
                },
            )

        # Every target counted, and summed. A command with three arguments deletes all three, so the
        # blast radius is the total rather than the largest.
        total = 0
        parts: list[dict[str, object]] = []
        for path in read.paths:
            one = self._fs.resolve(path, ctx)
            if one.magnitude is None:
                # One unreadable target makes the whole command unsizeable. Counting the rest would
                # report a number smaller than what the command touches.
                return Resolution.unresolved(
                    self.unit,
                    reason=str(one.evidence.get("reason") or "target_unresolved"),
                    evidence={"command": target[:200], "target": path},
                )
            total += one.magnitude
            parts.append({"target": path, "objects": one.magnitude})

        from datetime import UTC, datetime

        return Resolution.resolved(
            self.unit,
            total,
            direction=Direction.EXACT,
            resolved_at=datetime.now(UTC),
            consistency="strong",
            evidence={"form": read.reason, "targets": parts, "command": target[:200]},
        )

    def reachable_max(self, ctx: ResolveContext) -> Resolution:
        """The same bound `fs.paths` reports: one command can reach the whole declared root."""
        return self._fs.reachable_max(ctx)
