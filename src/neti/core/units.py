"""Canonical magnitude units.

One ceiling grammar across every resolver. A ceiling is always `(unit, integer)`, never a bare
number, because "above 500" means nothing without knowing whether that is people or bytes.

Units are deliberately closed. Adding one is a decision about the ceiling grammar, not an
implementation detail, so it happens here and shows up in every policy file that uses it.
"""

from __future__ import annotations

from enum import StrEnum


class Unit(StrEnum):
    """What a resolved magnitude counts."""

    PRINCIPALS = "principals"
    """People or service identities affected. The identity-blast-radius unit."""

    APPS = "apps"
    """Application assignments lost or granted."""

    RECIPIENTS = "recipients"
    """Message destinations. Distinct from PRINCIPALS: an external address is a recipient
    without being a directory principal."""

    OBJECTS = "objects"
    """Files, records, rows-as-opaque-items — anything countable and enumerable."""

    BYTES = "bytes"
    """Data volume."""

    ROWS = "rows"
    """Database rows. Separate from OBJECTS because its resolvers are estimate-based and
    biased low; see SCOPE.md NC-10."""

    RESOURCES = "resources"
    """Infrastructure resources in a plan — the Terraform/IaC unit.

    Separate from OBJECTS rather than folded into it, because a ceiling has to mean something
    specific to the person declaring it. "Block above 50 objects" is unreadable when the same
    number would be reasonable for files and catastrophic for load balancers."""


class Direction(StrEnum):
    """How a resolved magnitude relates to the true value.

    Declared by the resolver, never inferred. See RESOLVER_CONTRACT.md rule 1.
    """

    EXACT = "exact"

    UPPER_BOUND = "upper_bound"
    """True value is <= magnitude. Sound for ALLOW (if measured is under the ceiling, the truth is
    too). NOT sound for BLOCK: the truth could be under the ceiling, so a block may over-block."""

    LOWER_BOUND = "lower_bound"
    """True value is >= magnitude. Sound for BLOCK (if measured is over the ceiling, the truth is
    too). NOT sound for ALLOW: the truth could be arbitrarily larger."""


def may_allow(direction: Direction) -> bool:
    """A sound ALLOW needs `true <= measured`, so that being under the ceiling is conclusive."""
    return direction in (Direction.EXACT, Direction.UPPER_BOUND)


def may_block(direction: Direction) -> bool:
    """A sound BLOCK needs `true >= measured`, so that being over the ceiling is conclusive.

    Blocking on an UPPER_BOUND is *safe* but may over-block, which is why it is permitted and
    recorded (`over_block_possible`) rather than forbidden. BigQuery's `maximum_bytes_billed` does
    exactly this and documents the caveat: actual bytes scanned can be lower than the estimate
    because block pruning happens at execution, not planning.
    """
    return direction in (Direction.EXACT, Direction.LOWER_BOUND)
