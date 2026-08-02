"""How many files does this path touch.

The resolver that makes every other seam mean something. Twenty real Claude Code tool calls were
recorded through the `PreToolUse` hook and every single verdict was `allow` by `unknown_tool` — not
because the gate was broken, but because nothing here could size a filesystem path. A seam without a
resolver is a place to write `allow`.

Coding agents are what most people run, and what they touch is files: `Glob`, `Edit`, `Write`, an
MCP filesystem server's `list_directory` and `search_files`, an apply-patch tool. This sizes those.

**It is exact, local, and strongly consistent**, which is a genuinely different position from the
directory resolvers. There is no provider, no token, no eventual-consistency window and no 800ms
budget shared with a network round trip. `RESOLVER_CONTRACT.md` was written against a
network-and-eventual-consistency case, and it degrades correctly to this one: `consistency` is
`strong` and there is no staleness bound to declare.

**Where it stops, and why that is safe.** Walking a very large tree is the one way this could become
slow, so the walk is capped. Past the cap the answer is a `LOWER_BOUND` — we counted at least this
many and stopped — and the decision procedure already knows what to do with that: a lower bound can
soundly *block* (measured over the ceiling means the truth is too) but can never soundly *allow*
(measured under it proves nothing). An under-ceiling result with the cap hit escalates to the
declared `on_unbounded` verdict instead of sailing through. The cap is a latency control that cannot
turn into a permissive answer.

**What it counts, for anyone checking the number.** Every directory entry that is not a directory —
so regular files *and* symlinks, because deleting a symlink is a real effect even though it is a
small one. The obvious cross-check disagrees for exactly that reason:

    find . -type f | wc -l                  35,807   regular files only
    find . \\( -type f -o -type l \\) | wc -l   35,840   what this resolver reports

Use the second one. The first is the check most people reach for and it will look like a 33-file
overcount.

**What it deliberately does not do.** It sizes *structured path arguments*. It does not parse a
shell command to work out what `rm -rf` would remove. That is a syntactic gate over an unbounded
grammar, and `SCOPE.md` NC-10 already takes that position about SQL predicates: a gate guessing at a
string's meaning makes a weaker claim than one reading a value. A `Bash` tool stays ungated and out
of scope (NC-09) rather than gated badly.
"""

from __future__ import annotations

import contextlib
import glob as globlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from neti.core.types import Resolution
from neti.core.units import Direction, Unit
from neti.resolvers.base import ResolveContext

__all__ = ["DEFAULT_CAP", "FilesystemResolver"]

DEFAULT_CAP = 200_000
"""Files to enumerate before giving up and reporting a lower bound.

Chosen so that the pathological case — a policy pointed at `/` — costs well under a second rather
than minutes. A repository with more than 200,000 files is already past every ceiling anyone would
declare, so the exact number stops mattering long before the cap is reached.
"""

_GLOB_CHARS = ("*", "?", "[")


@dataclass
class FilesystemResolver:
    """Counts the files a path, directory or glob addresses.

    `root` is what `neti inventory` reports as reachable. Without one the honest answer to "what
    could this reach" is "every file this process can read", which is not a number — so it resolves
    UNRESOLVED and says so, rather than inventing a bound nobody can defend.
    """

    root: Path | None = None
    cap: int = DEFAULT_CAP

    unit: ClassVar[Unit] = Unit.OBJECTS
    breakdown_keys: ClassVar[frozenset[str]] = frozenset({"bytes"})
    """Total size alongside the count, so a policy can band on either. Ten files can be a bigger
    deletion than ten thousand, and which one matters is the operator's call, not ours."""

    def resolve(self, target: str, ctx: ResolveContext) -> Resolution:
        del ctx  # local and synchronous: nothing here can time out against a provider
        if not target:
            return Resolution.unresolved(self.unit, reason="empty_path")

        expanded = os.path.expanduser(target)
        try:
            if any(ch in expanded for ch in _GLOB_CHARS):
                return self._count_glob(expanded)
            return self._count_path(Path(expanded))
        except OSError as exc:
            # A path we cannot read is not a path with no files in it. Saying UNRESOLVED sends this
            # through the declared `on_unresolved`; reporting 0 would turn a permissions error into
            # a permissive verdict.
            return Resolution.unresolved(
                self.unit, reason="path_unreadable", evidence={"error": str(exc), "path": target}
            )

    def reachable_max(self, ctx: ResolveContext) -> Resolution:
        del ctx
        if self.root is None:
            return Resolution.unresolved(
                self.unit,
                reason="no_root_declared",
                evidence={
                    "hint": (
                        "declare a root for this resolver to report what it could reach; without "
                        "one the answer is every file this process can read, which is not a bound"
                    )
                },
            )
        return self._count_path(self.root)

    # ------------------------------------------------------------------ internals

    def _count_path(self, path: Path) -> Resolution:
        if not path.exists():
            # Distinct from unreadable, and distinct from empty. A tool asked to act on a path that
            # is not there will fail on its own; the gate should not pretend to have sized it.
            return Resolution.unresolved(
                self.unit, reason="path_not_found", evidence={"path": str(path)}
            )
        if path.is_file():
            return self._resolved(1, path.stat().st_size, Direction.EXACT, {"path": str(path)})

        files = 0
        total_bytes = 0
        capped = False
        seen: set[tuple[int, int]] = set()

        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            # A directory reached twice is a symlink cycle or a bind mount. Counting it twice would
            # inflate the magnitude, and following it forever would hang the gate.
            try:
                stat = os.stat(dirpath)
            except OSError:
                dirnames[:] = []
                continue
            key = (stat.st_dev, stat.st_ino)
            if key in seen:
                dirnames[:] = []
                continue
            seen.add(key)

            for name in filenames:
                files += 1
                # Counted, possibly unsized: the file exists, and existing is what the magnitude
                # is about. A stat that fails must not drop it from the count.
                with contextlib.suppress(OSError):
                    total_bytes += os.lstat(os.path.join(dirpath, name)).st_size
                if files >= self.cap:
                    capped = True
                    break
            if capped:
                break

        direction = Direction.LOWER_BOUND if capped else Direction.EXACT
        return self._resolved(
            files,
            total_bytes,
            direction,
            {"path": str(path), "capped": True, "cap": self.cap} if capped else {"path": str(path)},
        )

    def _count_glob(self, pattern: str) -> Resolution:
        matches = globlib.glob(pattern, recursive=True)
        files = [m for m in matches if os.path.isfile(m)]
        total = 0
        for m in files[: self.cap]:
            with contextlib.suppress(OSError):
                total += os.lstat(m).st_size
        capped = len(files) > self.cap
        return self._resolved(
            min(len(files), self.cap) if capped else len(files),
            total,
            Direction.LOWER_BOUND if capped else Direction.EXACT,
            {"pattern": pattern, "matched": len(matches)},
        )

    def _resolved(
        self, files: int, total_bytes: int, direction: Direction, evidence: dict[str, object]
    ) -> Resolution:
        return Resolution.resolved(
            self.unit,
            files,
            direction=direction,
            # Stamped on the resolution, never read by the decision — `neti.core` stays clock-free
            # and a stored decision replays to the same verdict. RESOLVER_CONTRACT.md rule 3.
            resolved_at=datetime.now(UTC),
            consistency="strong",
            breakdown={"bytes": total_bytes},
            evidence=evidence,
        )
