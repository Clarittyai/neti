"""What to protect on day zero, before anybody has seen any traffic.

A fresh install used to protect nothing and could not, until somebody edited a 210-line YAML file:
nine gated parameters, **zero** ceilings, observe mode. The path to first protection was eight steps
and a week, and the median install never finished it.

**The founding principle is right about the wrong thing.** *"You cannot pick a ceiling before you
have seen your own numbers"* is true of a **tuned** ceiling — the kind that has to sit just above
ordinary work, where being wrong means interrupting somebody all day. It is not true of a
catastrophic one. Nobody's normal workflow deletes twenty thousand files or reads `~/.ssh`, and
waiting a week to say so protects nothing in the meantime.

## The line that keeps this honest

**Day zero never blocks on a number we chose.**

Everything derived from a size here is `flag` — recorded, notified, and the call proceeds. Two
day-zero verdicts *stop* a call, and neither is a number: an identity match on a file the operator
was shown by name, and a target outside the directory the agent was pointed at. One is what the
thing is, the other is where it is, and both are checkable against the operator's own disk.
Numbers somebody has to defend at 2am still come from their own traffic, through `neti propose`.

The location rule is the one that reaches what the scan cannot. `sensitive:` is built by walking the
project, so it never names `~/.ssh/id_rsa` or `~/.aws/credentials` — the two files most worth
protecting are outside the only tree we looked at, are one object each, and sit under every ceiling
anybody would write. Nothing but *where it is* sees them.

So *"you are never asked to guess a number"* stays true, and we do not quietly guess one either.

## Why the threshold is not a guess

It is anchored on the reach `neti start` already measures — a fact about this machine, structural
rather than behavioural, available in the first thirty seconds. `max(FLOOR, reach // 10)` behaves
across four orders of magnitude:

    a tiny script repo         24 reachable  →  flag above    500   nothing ordinary trips it
    a small service         5,011 reachable  →  flag above    501
    this repository        59,330 reachable  →  flag above  5,933
    a monorepo          1,200,000 reachable  →  flag above 120,000

The floor is what stops a twenty-four-file repository flagging its own three-file glob, which is the
noise that would get this uninstalled in an afternoon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from neti.insight.secrets_scan import Candidate, scan

__all__ = ["FLOOR", "SHARE", "Preset", "build", "session_budget", "threshold"]

FLOOR = 500
"""Below this, nothing flags. A small repository has nothing catastrophic to do, and a gate that
fires on its ordinary work is a gate that is gone by Friday."""

SHARE = 10
"""Flag a call that would touch more than a tenth of everything one call could reach.

A share rather than a constant because "big" is a property of the tree. Ten percent is a judgement,
and it is the only one in this module — stated here rather than buried in an expression."""


def threshold(reach: int) -> int:
    """The size above which a call is worth telling somebody about, on this machine."""
    return max(FLOOR, reach // SHARE)


def session_budget(reach: int) -> int:
    """The cumulative total, across one session, worth mentioning.

    **One session should not touch more of the tree than the tree contains.** That is the anchor,
    and like the per-call one it is a fact about this machine rather than a guess: exceeding it
    means the agent has read or written everything at least once, which is the shape of a runaway
    loop or a refactor nobody asked for.

    This is the only thing that sees the 56%. Measured over a simulated week, 178 of 320 calls were
    single-file operations of magnitude 1 — a per-call ceiling is structurally blind to all of them,
    and two hundred edits of one file each is exactly how an agent rewrites a codebase by accident.
    """
    return max(FLOOR, reach)


@dataclass(frozen=True)
class Preset:
    """The day-zero policy, as data. Rendered by the caller, never applied by this module."""

    reach: int
    flag_above: int
    session_above: int = 0
    off_limits: list[Candidate] = field(default_factory=list)

    @property
    def protects_anything(self) -> bool:
        """Whether this is worth writing at all.

        Anything we could measure. The first version skipped a repository whose reach was under the
        floor, on the grounds that a 500-object threshold cannot fire in a six-file tree — which is
        true today and wrong tomorrow, because repositories grow and the written number does not
        move on its own. A threshold is a watch, not a claim; leaving it out means somebody who ran
        `neti start` on a young project silently never gets one.

        Nothing is written at all when there was nothing to measure — no reach means no filesystem
        we could read, and a number invented over that would be exactly the guess this module
        refuses to make.
        """
        return self.reach > 0 or bool(self.off_limits)

    def as_policy(self) -> dict[str, Any]:
        """The shape `config/policy.py` accepts, for a test to load and check."""
        return {
            "sensitive": [
                {"match": c.match, "verdict": c.verdict, "why": c.why} for c in self.off_limits
            ],
            "bands": [{"above": self.flag_above, "verdict": "flag"}],
        }


def build(root: str | Path, reach: int) -> Preset:
    """What to protect here, from what is on this disk and how much of it there is.

    `reach` comes from the caller because `neti start` has already paid for it — building an
    inventory twice on a large tree to save passing an integer would be a poor trade.
    """
    try:
        found = scan(root)
    except OSError:
        # A tree we cannot walk is a tree we say nothing about. The size threshold still stands:
        # it depends on a number already in hand, not on a second walk.
        found = []
    return Preset(
        reach=reach,
        flag_above=threshold(reach),
        session_above=session_budget(reach),
        off_limits=found,
    )
