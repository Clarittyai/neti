"""Is this target outside the directory the agent was pointed at?

The day-zero preset puts `.env` off limits by scanning the project — and the agent's most valuable
secrets are not in the project. `~/.ssh/id_rsa`, `~/.aws/credentials`, `~/.config/gh/hosts.yml` are
all one object, all outside the root, and all silently allowed:

    Read(.env)                        ASK
    Read(~/.ssh/id_rsa)               ALLOWED, silently
    Read(~/.aws/credentials)          ALLOWED, silently
    Read(../../../etc/passwd)         ALLOWED, silently

Three separate things had to line up for that. `secrets_scan` walks the root, so no `sensitive:`
rule ever names them. `providers.fs.root` bounds what `reachable_max` reports but not what
`resolve` will size, so the read succeeds. And one file is under every ceiling anybody would write.

**This is location, not magnitude.** A target outside the tree you pointed the agent at is a fact
about where it is, checkable without reading anything, and not a number we chose — which is why it
is allowed to stop a call under the same rule that lets an off-limits *file* stop one.

**Why a read is the harm.** Sizing does not help here and neither does flagging: an agent that reads
`~/.aws/credentials` has put it in the context window, and the context window goes to the model
provider. There is no after-the-fact for that one.

**Why this lives with the resolvers and not in `core`.** It touches the filesystem — `resolve()`
follows symlinks, which is the only way `project/link -> ~/.ssh` is recognisable as an escape. That
makes it a *measurement*, and `neti.core` performs no I/O by invariant, because the replay contract
depends on a stored decision reproducing from stored evidence. It was written in `core` first and
caught by `test_core_is_pure` — the test doing exactly its job: a decision that re-read the
filesystem would answer differently tomorrow and the audit record's whole claim would collapse.

So it is measured here, and `decide` consumes the fact — the same shape as every magnitude.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

__all__ = ["outside"]

_GLOB = ("*", "?", "[")


def _literal_prefix(target: str) -> str:
    """The part of a glob that is a real path. `src/**/*.ts` -> `src`.

    A pattern is anchored by whatever comes before its first wildcard, and that prefix is what
    decides where it can reach. Nothing here expands the glob — expanding it is the resolver's job
    and costs a walk.
    """
    parts: list[str] = []
    for part in Path(target).parts:
        if any(ch in part for ch in _GLOB):
            break
        parts.append(part)
    return str(Path(*parts)) if parts else ""


def outside(target: str, root: str | Path | None) -> bool:
    """Whether this target lies outside `root`.

    `None` root means nothing was declared, so there is no inside to be outside of and the answer
    is no — the same shape as every other undeclared thing here.

    The system temp directory is exempt. Scratch files are ordinary agent work, they are not
    somebody's credentials, and confirming every one of them is how a control gets switched off.
    """
    if not root or not target:
        return False

    prefix = _literal_prefix(target)
    if not prefix:
        return False

    try:
        base = Path(root).expanduser().resolve()
        # `resolve()` follows `..` and symlinks, which is the point — `../../../etc/passwd` is only
        # recognisable as an escape after it is normalised.
        here = Path(os.path.expandvars(prefix)).expanduser()
        here = (base / here).resolve() if not here.is_absolute() else here.resolve()
    except (OSError, RuntimeError, ValueError):
        # Unresolvable is not evidence of being inside. Saying "outside" here would be the safe
        # direction, but it would also fire on every path that simply does not exist yet, which is
        # most of what `Write` does.
        return False

    if here.is_relative_to(base):
        return False

    # The temp exemption applies only when the project is *not itself* in the temp directory.
    # Otherwise it swallows everything: a checkout under `/tmp` would make every sibling there
    # invisible, including one holding somebody's keys. Found by testing it — pytest's `tmp_path`
    # lives under the system temp directory, so every escape a test could construct was exempt and
    # the check silently passed everything.
    try:
        temp = Path(tempfile.gettempdir()).resolve()
        if base.is_relative_to(temp):
            return True
        return not here.is_relative_to(temp)
    except (OSError, ValueError):
        return True
