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

**Declining is not the same as saying "nothing to see here", and this file answers both questions.**
`npm test` and `cat list.txt | xargs rm` were both UNRESOLVED, so one policy hook had to give them
one verdict: allow, and a deletion passes in silence; confirm, and every ordinary command needs a
human. So alongside the size, this reports whether what it could not size *looked destructive* —
`destructive_signal` below — and `on_unsized_risk` decides that case separately.

Note that the two answers lean opposite ways on purpose. Sizing declines when it is unsure, because
a wrong number is unsound. Flagging fires when it is unsure, because surfacing a command costs a
line in a report and missing one costs the deletion. They are conservative in opposite directions
because their errors are not the same error.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from neti.core.types import Resolution
from neti.core.units import Direction, Unit
from neti.resolvers.base import ResolveContext
from neti.resolvers.filesystem import DEFAULT_CAP, FilesystemResolver

__all__ = ["ShellPathsResolver", "destructive_signal", "referenced_paths", "targets_of"]

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

# --- the second question: did this destroy something, even where the size is unknowable -----------

# Wider than `_DESTRUCTIVE` above, because this list does not have to produce a number — only to
# recognise a command that removes or overwrites something. `dd` and `mkfs` write over a device,
# `rmdir` removes directories, `git rm` removes tracked files.
_DESTRUCTIVE_VERBS = _DESTRUCTIVE | {"dd", "mkfs", "rmdir"}

# Everything that can sit in front of the real verb without changing what it destroys. `xargs` is
# the load-bearing one: `cat list.txt | xargs rm` is the exact command that motivated all of this.
_PREFIXES = frozenset(
    {"sudo", "command", "env", "nohup", "time", "exec", "xargs", "then", "do", "else", "!"}
)

# Shells that take a command line as an argument. `bash -c 'rm -rf /tmp/x'` is one token away from
# `rm -rf /tmp/x` and no less destructive for it, so the signal reads inside. Sizing deliberately
# does not: it would have to reason about a second layer of quoting to produce a number, and a
# number produced that way is exactly the kind this file refuses to invent.
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})

# Whether a backslash is an escape character or a path separator. See `_split`.
_POSIX = os.name != "nt"

# Where one command ends and the next begins, outside quotes. `_segments` below does the walk,
# because a regex cannot tell `a | b` from `echo "a | b"` and the difference decides whether
# `bash -c 'find / | xargs rm'` is read as one command or shredded into fragments.
_SEPARATORS = frozenset({"|", ";", "&", "\n", "(", ")", "`"})

# `>` truncates. `>>` appends, `2>&1` and `->` are not redirects at all.
_REDIRECT = re.compile(r"(?<![>\-0-9])>(?![>&=])\s*(\S+)")


@dataclass(frozen=True)
class Signal:
    """A command that destroys something, named by the form that gave it away."""

    form: str
    path: str | None = None
    """For a truncating redirect, where it points. The resolver checks whether that file exists:
    `pytest -q > out.txt` creates a file and destroys nothing, and flagging it would spend the
    operator's attention on the most common redirect there is."""


def _segments(text: str) -> list[str]:
    """Split on unquoted command separators.

    Splitting is what makes the signal read *command position* rather than the whole line —
    `grep -rn 'rm' src/` mentions the verb and does not run it. Quote tracking is what keeps that
    true in the other direction: the argument of `bash -c` is one command, not four fragments, and
    a naive split turned the most obvious wrapper there is back into silence.
    """
    out: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for ch in text:
        if quote is not None:
            # The quote characters are kept, not consumed. Stripping them would hand
            # `bash -c 'find / | xargs rm'` to `shlex` as loose words, so the inner command would
            # stop being one argument — which is exactly how the most obvious wrapper there is
            # went quiet the first time this was written.
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in {"'", '"'}:
            quote = ch
            current.append(ch)
            continue
        if ch in _SEPARATORS:
            out.append("".join(current))
            current = []
            continue
        current.append(ch)
    out.append("".join(current))
    return out


def _head(segment: str) -> tuple[str, list[str]]:
    """The verb a segment actually runs, with its prefixes peeled, and the rest of its tokens."""
    try:
        tokens = _split(segment)
    except ValueError:
        tokens = segment.split()
    saw_prefix = False
    while tokens:
        token = tokens[0]
        if token in _PREFIXES:
            saw_prefix, tokens = True, tokens[1:]
            continue
        # `xargs -0 -n1 rm` — flags belonging to a prefix, not to the verb.
        if saw_prefix and _is_flag(token):
            tokens = tokens[1:]
            continue
        break
    if not tokens:
        return "", []
    return tokens[0].rsplit("/", 1)[-1], tokens[1:]


def destructive_signal(command: str) -> Signal | None:
    """Does this command destroy something, whether or not its size is knowable?

    Textual, and deliberately shallow: it recognises forms, it does not simulate. What it buys is
    the difference between *"this is not a filesystem deletion"* and *"this is a deletion and I
    cannot tell you how big"*, which no amount of `on_unresolved` tuning could express.

    A wrapper script is still invisible to it — `./cleanup.sh` deletes and says nothing about it,
    and no textual signal will ever see that. SCOPE.md NC-15 says so rather than leaving it
    implied.
    """
    return _signal(command or "", depth=0)


def _subcommand_signal(verb: str, rest: list[str]) -> Signal | None:
    """Destructive forms that need a flag or a subcommand to be one.

    Every entry here widens the **flag** half only. Nothing below teaches `targets_of` to produce a
    number, and that asymmetry is the design: flagging a command costs a line in `neti report`, and
    mis-sizing one lets a deletion through under a ceiling. So this list is allowed to grow much
    faster than the sizing table, and it leans toward saying *"this destroyed something and I cannot
    tell you how much"* wherever it is unsure.

    Each form is here because a coding agent reaches for it unprompted, and each one is paired with
    the spelling that is *not* destructive — `sed` without `-i`, `tee -a`, `rsync` without
    `--delete`. A recogniser that fired on both would teach an operator to stop reading the flag,
    which is the same reason `--force-with-lease` is deliberately absent from the `git push` check.

    Deliberately **not** here: `chmod -R` and `chown -R`. They can make a tree unusable and they
    destroy no data, and folding "broke the permissions" into the same signal as "deleted the files"
    makes the signal mean less. That is a judgement, and it is written down rather than implied.
    """
    flags = {t for t in rest if _is_flag(t)}
    first = rest[0] if rest else ""

    # In-place edit. The most common way an agent overwrites a file without ever calling `rm`.
    #
    # `-i`, `-i.bak`, and bundled short clusters like `-ni`. sed's short flags are
    # n/e/f/i/E/r/s/u/z, so an `i` inside a cluster is unambiguous — and `--include` is not a
    # cluster, so the long-flag spelling cannot be caught by accident.
    if verb == "sed" and any(_has_short(f, "i") or f == "--in-place" for f in flags):
        return Signal(form="sed_in_place")

    # `tee f` truncates `f`; `tee -a f` does not. The default is the destructive one.
    #
    # A bare `tee` in a pipeline writes to stdout and destroys nothing, so a file argument is
    # required — otherwise the single most common use of the command is flagged forever, which is
    # how a report stops being read.
    if verb == "tee" and not (flags & {"-a", "--append"}) and any(not _is_flag(t) for t in rest):
        return Signal(form="tee_truncate")

    if verb == "rsync" and any(f.startswith("--delete") for f in flags):
        return Signal(form="rsync_delete")

    if verb == "git" and first == "branch" and any("D" in f.lstrip("-") for f in flags):
        # `-D` only. `-d` refuses to drop an unmerged branch, which is exactly the distinction
        # worth preserving.
        return Signal(form="git_branch_force_delete")

    if verb == "git" and first == "stash" and len(rest) > 1 and rest[1] in {"drop", "clear"}:
        return Signal(form=f"git_stash_{rest[1]}")

    if verb == "docker":
        if first in {"rmi", "rm"} or (len(rest) > 1 and rest[0] == "volume" and rest[1] == "rm"):
            return Signal(form="docker_remove")
        if "prune" in rest:
            return Signal(form="docker_prune")

    if verb == "kubectl" and first == "delete":
        return Signal(form="kubectl_delete")

    if verb == "terraform" and (first == "destroy" or "-destroy" in rest):
        return Signal(form="terraform_destroy")

    # Object-store deletion. Reaches far more data per keystroke than anything on a local disk, and
    # `storage.objects` can size the prefix — but only once somebody declares it, so the signal has
    # to stand on its own until then.
    if verb == "aws" and first == "s3" and len(rest) > 1 and rest[1] in {"rm", "rb"}:
        return Signal(form=f"aws_s3_{rest[1]}")
    if verb in {"gsutil", "gcloud"} and "rm" in rest[:2]:
        return Signal(form="gsutil_rm")

    return None


def _signal(text: str, *, depth: int) -> Signal | None:
    for segment in _segments(text):
        if not segment.strip():
            continue
        verb, rest = _head(segment)
        if verb in _SHELLS and depth < 2:
            # `-c`, and also `-lc` / `-ec`: bundled short flags are how this is usually written.
            at = next(
                (i for i, t in enumerate(rest) if _is_flag(t) and "c" in t.lstrip("-")),
                None,
            )
            inner = rest[at + 1 :] if at is not None else []
            nested = _signal(inner[0], depth=depth + 1) if inner else None
            if nested is not None:
                return Signal(form=f"{verb}_c:{nested.form}", path=nested.path)
        if verb in _DESTRUCTIVE_VERBS:
            return Signal(form=f"destructive_verb:{verb}")
        if verb == "find" and (
            "-delete" in rest
            or ("-exec" in rest and any(t.rsplit("/", 1)[-1] in _DESTRUCTIVE_VERBS for t in rest))
        ):
            return Signal(form="find_delete")
        flagged = _subcommand_signal(verb, rest)
        if flagged is not None:
            return flagged
        if verb == "git" and rest:
            sub = rest[0]
            if sub == "rm" or (sub == "clean" and any(_is_flag(f) and "f" in f for f in rest[1:])):
                return Signal(form=f"git_{sub}")
            if sub in {"checkout", "restore"} and "--" in rest[1:]:
                return Signal(form=f"git_{sub}")
            # `git checkout -- .` was recognised and `git reset --hard` was not, which is arbitrary:
            # they destroy the same thing. These two are the git verbs that lose work nothing else
            # gets back — uncommitted changes, and published history other people have pulled.
            if sub == "reset" and "--hard" in rest[1:]:
                return Signal(form="git_reset_hard")
            if sub == "push" and any(f in {"--force", "-f"} for f in rest[1:]):
                # `--force-with-lease` is deliberately not here. It is the form that refuses to
                # overwrite work it has not seen, and a gate that treats the careful spelling and
                # the reckless one alike teaches people to stop distinguishing them.
                return Signal(form="git_push_force")

    redirect = _REDIRECT.search(text)
    if redirect:
        return Signal(form="truncating_redirect", path=redirect.group(1))
    return None


def referenced_paths(command: str, depth: int = 0) -> tuple[str, ...]:
    """Every path-like argument of a command, whatever the command is.

    **For WHERE and WHAT, never for HOW MANY.** Nothing here is counted: these are handed to the
    location and identity axes, which ask what a target *is* and where it lives. A magnitude read
    off this would be a guess about what the command does with each path, and this deliberately
    does not know.

    The gap it closes, found by running the shipped product against itself:

        Read(~/.ssh/id_rsa)              stopped — outside the declared root
        Bash(cp ~/.ssh/id_rsa /tmp/x)    ALLOWED

    Same file, same session, one `Bash` away. `targets_of` above only parses the verbs that
    *destroy*, because magnitude was the only axis a command had — so every other verb's arguments
    were invisible, and reading a key is not destruction. The protection was one shell call from
    being worthless.

    Compound commands are walked rather than declined, which is the opposite of `targets_of` and
    correct for the opposite reason: refusing to count `a | b` is caution, because a wrong number
    is worse than none, but refusing to *look* at its arguments is just not looking.

    What is deliberately skipped, each because including it would cost more than it caught:

    - anything with `$` or a backtick — the shell will expand it to something this cannot know
    - anything with whitespace — `git commit -m "fix the ~/.ssh loader"` is one token, and it is
      prose, not a path
    - flags, and the value of a flag that takes one

    A `~` is expanded, because that is the whole point: `~/.ssh/id_rsa` is only recognisable as an
    escape once it is a real path.
    """
    seen: dict[str, None] = {}
    for segment in _segments(command or ""):
        if not segment.strip():
            continue
        try:
            tokens = _split(segment)
        except ValueError:
            continue
        if not tokens:
            continue

        # `bash -c "cat ~/.ssh/id_rsa"` is one quoted token to `shlex`, so every path inside it was
        # invisible. `destructive_signal` already descends here for the same reason; this did not,
        # and a red-team pass found the two disagreeing. Depth-limited, because `bash -c "bash -c
        # …"` is a shape somebody will eventually send.
        verb = tokens[0].rsplit("/", 1)[-1]
        if verb in _SHELLS and depth < 2:
            at = next(
                (i for i, t in enumerate(tokens) if _is_flag(t) and "c" in t.lstrip("-")), None
            )
            if at is not None and at + 1 < len(tokens):
                for nested in referenced_paths(tokens[at + 1], depth=depth + 1):
                    seen.setdefault(nested, None)

        for token in tokens[1:]:
            path = _as_path(token)
            if path is not None:
                seen.setdefault(path, None)

    # `echo x > /etc/hosts` names its target in the redirect, not in the argument list.
    for match in _REDIRECT.finditer(command or ""):
        path = _as_path(match.group(1))
        if path is not None:
            seen.setdefault(path, None)

    return tuple(seen)


def _as_path(token: str) -> str | None:
    """One token as a filesystem path, or `None` if it is not one.

    The `@` strip is `curl -d @~/.aws/credentials`, which is a real way to post a file somewhere and
    reads as a flag value otherwise. The `://` skip is the other half of the same command: a URL
    contains slashes and is not a path, and while resolving one relative to the root happens to
    land inside it and stay silent, relying on that is relying on an accident.
    """
    if not token or _is_flag(token):
        return None
    if any(c.isspace() for c in token) or "`" in token or "://" in token:
        return None

    token = token[1:] if token.startswith("@") else token

    if "$" in token:
        # `cat $HOME/.ssh/id_rsa` is a spelling an agent really produces, and it was invisible.
        # Expanded rather than refused, which is what `location.outside` already does to the same
        # strings — the two disagreeing is how a path gets judged in one place and not the other.
        #
        # A variable this process cannot see stays refused: an unset name expands to nothing and
        # would turn `$OUT/file` into `/file`, which is a different path from the one that will run.
        token = os.path.expandvars(token)
        if "$" in token:
            return None

    if not token or token in {".", ".."}:
        return None
    # A bare name with a dot in it — `neti.yaml`, `.env`, `package.json`. Without this, `rm
    # neti.yaml` and `sed -i '' s/enforce/observe/ neti.yaml` were invisible: the gate's own policy
    # file sits in the project root, so it is named with no slash in front of it, and every rule
    # written to protect it could not see the command that deletes it.
    #
    # A dot is the discriminator because it is what separates a filename from a subcommand:
    # `npm test`, `git status`, `cargo build` have none. `requests==2.3.1` has one and is read as a
    # path, which costs nothing — it resolves inside the root and matches no rule.
    if "/" not in token and not token.startswith("~") and "." not in token:
        return None

    # Only a real `~` — `~` alone or `~/…`. `~)` came out of `$(echo ~)` after the segment split and
    # `expanduser` turned it into the home directory, so a command that never named home was
    # recorded as reaching for it. A wrong path in an audit record is worse than a missing one.
    if token == "~" or token.startswith("~/"):
        try:
            return str(Path(token).expanduser())
        except RuntimeError:
            # No home directory this process can determine. Nothing to expand against, so nothing
            # to say — never a crash, on a path that runs before every tool call.
            return None
    if token.startswith("~"):
        return None
    return token


@dataclass(frozen=True)
class Targets:
    """What a command was read as touching, and whether it was read at all."""

    paths: tuple[str, ...] = ()
    understood: bool = False
    reason: str = "not_a_recognised_destructive_command"


def _split(text: str) -> list[str]:
    """`shlex.split`, without eating the separators out of a Windows path.

    **`shlex.split` defaults to POSIX mode, where `\\` is an escape character.** On Windows that
    turns `rm -rf C:\\Users\\me\\build` into the single token
    `C:UsersmebuildC` — a path that does not exist, so `fs.paths` cannot size it, so the call
    resolves UNRESOLVED, so the shipped policy's `on_unresolved: allow` lets it through.

    Which means: **`shell.paths` could not size any deletion on Windows.** Not "sized it wrongly" —
    could not see it at all, while reporting the same "not a recognised destructive command" note
    that `npm test` gets. The gate looked identical and gated nothing.

    It was invisible for the ordinary reason: development happens on macOS, where the same string is
    a POSIX path with no backslashes in it, and CI's Windows job has never been green.

    So the mode follows the platform, because the platform *is* the difference — a backslash is an
    escape in `sh` and a separator in `cmd`, and no reading is right in both. Non-POSIX mode
    leaves quote characters attached to their tokens, so those come off here; nothing else about
    the parse changes, and on any non-Windows machine this is exactly `shlex.split` as before.
    """
    if _POSIX:
        return shlex.split(text)
    return [_unquote(token) for token in shlex.split(text, posix=False)]


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return token[1:-1]
    return token


def _is_flag(token: str) -> bool:
    return token.startswith("-") and token != "-"


def _has_short(flag: str, letter: str) -> bool:
    """Is `letter` present in a bundled short-flag cluster like `-ni`?

    Long flags are excluded on purpose: `--include` must not read as `-i`, and a cluster is by
    definition a single dash.
    """
    return flag.startswith("-") and not flag.startswith("--") and letter in flag[1:]


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
        tokens = _split(text)
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

    def _unsized(self, command: str, reason: str, **extra: object) -> Resolution:
        """UNRESOLVED, plus whether what could not be sized looked destructive.

        The flag rides on `evidence`, which is already part of every resolution and already inside
        the record. No new `ResolutionState` — `PARTIAL` means *enumeration was truncated* and
        repurposing it would corrupt the one signal that says a magnitude is a floor.
        """
        signal = destructive_signal(command)
        if (
            signal is not None
            and signal.form == "truncating_redirect"
            and signal.path
            # Only a redirect over a file that is already there destroys anything.
            and self._fs.resolve(signal.path, ResolveContext()).magnitude is None
        ):
            signal = None
        evidence: dict[str, object] = {"command": command[:200], **extra}
        if signal is None:
            evidence["note"] = "not a recognised destructive command; on_unresolved decides"
        else:
            # The form itself, not a bare `True`. One key carries both facts — *that* it destroys
            # and *what* gave it away — so the record has one place to look and `neti report` can
            # print "destructive_verb:rm" rather than the word "destructive".
            evidence["destructive"] = signal.form
            evidence["note"] = "destructive but unsizeable; on_unsized_risk decides"
        # Present even here — especially here. `cp ~/.ssh/id_rsa /tmp/x` is not a deletion, has no
        # magnitude and never will; what makes it worth stopping is *which file it names*, and that
        # is knowable when the size is not.
        referenced = referenced_paths(command)
        if referenced:
            evidence["referenced"] = list(referenced)
        return Resolution.unresolved(self.unit, reason=reason, evidence=evidence)

    def resolve(self, target: str, ctx: ResolveContext) -> Resolution:
        read = targets_of(target)
        if not read.understood:
            return self._unsized(target, read.reason)

        # Every target counted, and summed. A command with three arguments deletes all three, so the
        # blast radius is the total rather than the largest.
        total = 0
        parts: list[dict[str, object]] = []
        for path in read.paths:
            one = self._fs.resolve(path, ctx)
            if one.magnitude is None:
                # One unreadable target makes the whole command unsizeable. Counting the rest would
                # report a number smaller than what the command touches. This branch is reached
                # *after* the parse understood the command, so it is destructive by construction —
                # `rm -rf $HOME/gone` is exactly the case the flag exists for.
                return self._unsized(
                    target,
                    str(one.evidence.get("reason") or "target_unresolved"),
                    target=path,
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
            evidence={
                "form": read.reason,
                "targets": parts,
                "command": target[:200],
                # A command this parser *understood* is destructive by construction — `targets_of`
                # recognises nothing else. The marker was only set on the unsizeable path, so a
                # gate whose deletions all resolved cleanly looked, to anything reading records,
                # exactly like a gate that had never seen a deletion. `neti propose` needs to know
                # the difference: deletions are rare, and a rule tuned for ordinary traffic will
                # wait forever for a sample size they never reach.
                "destructive": read.reason,
                "referenced": list(referenced_paths(target)),
            },
        )

    def reachable_max(self, ctx: ResolveContext) -> Resolution:
        """The same bound `fs.paths` reports: one command can reach the whole declared root."""
        return self._fs.reachable_max(ctx)
