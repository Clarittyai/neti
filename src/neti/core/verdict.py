"""The verdict and resolution-state lattices.

Verdicts are combined by JOIN — most restrictive wins. This is the single most important structural
property in the codebase: it makes the gate monotone, so more information can only ever tighten a
decision. A `meet` anywhere here would let an extra parameter or an extra fact turn a BLOCK into an
ALLOW, which is the defect that killed the earlier provenance design.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import IntEnum


class Verdict(IntEnum):
    """Ordered by severity. Integer values are the ordering and are part of the record format."""

    ALLOW = 0
    FLAG = 1
    """Recorded and surfaced; the call proceeds."""

    CONFIRM = 2
    """The call does not proceed without a human decision."""

    BLOCK = 3

    @property
    def proceeds(self) -> bool:
        """Whether the upstream call is made, in enforce mode."""
        return self <= Verdict.FLAG


def join(*verdicts: Verdict) -> Verdict:
    """Most-restrictive-wins. Empty input is ALLOW: nothing gated means nothing to object to."""
    return max(verdicts, default=Verdict.ALLOW)


def join_all(verdicts: Iterable[Verdict]) -> Verdict:
    return max(verdicts, default=Verdict.ALLOW)


class ResolutionState(IntEnum):
    """How much the resolver managed to learn. Ordered by decreasing knowledge."""

    RESOLVED = 0
    """An exact cardinality was obtained, subject to `Direction`."""

    PARTIAL = 1
    """Enumeration was truncated. UNMERGEABLE: never reduce this to a number, never sum it, never
    cache it. A truncated enumeration looks exactly like a small target, and under-counting is the
    dangerous direction. See RESOLVER_CONTRACT.md rule 2."""

    UNRESOLVED = 2
    """Timeout, 429, 5xx, expired credential, unsupported target type. We do not know."""

    @property
    def is_known(self) -> bool:
        return self is ResolutionState.RESOLVED


class Mode(IntEnum):
    """Deployment mode. OBSERVE is the default; see SCOPE.md and §3.1 of the plan."""

    OBSERVE = 0
    """Compute and record the verdict; always forward the call. Cannot block anything."""

    ENFORCE = 1
