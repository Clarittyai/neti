"""The Resolver protocol — RESOLVER_CONTRACT.md in code.

Everything outward-facing lives behind this interface: all I/O, all provider quirks, all failure.
`neti.core` never imports this module, and this module never imports `neti.core.decide`. That
one-way dependency is what keeps a decision replayable from stored resolutions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from neti.core.types import Resolution
from neti.core.units import Unit

__all__ = ["ResolveContext", "Resolver", "ResolverError", "UnsupportedTarget"]


class ResolverError(RuntimeError):
    """A resolver failed in a way that must surface as UNRESOLVED, not as an exception.

    Resolvers catch their own errors and return `Resolution.unresolved(...)`. This type exists for
    the cases a caller genuinely cannot proceed from — a missing credential, a misconfigured
    registry — where crashing loudly at startup beats failing closed on every call at runtime.
    """


class UnsupportedTarget(ResolverError):
    """The target exists but this resolver structurally cannot count it.

    The Exchange dynamic distribution group case. Distinct from "not found" because the honest
    answer differs: a DDG is a real group whose membership is computed by Exchange and never synced
    to Entra, so no amount of retrying will help and the operator needs to know that specifically.
    """


@dataclass(frozen=True)
class ResolveContext:
    """Everything a resolver needs that is not the target itself.

    `verified_domains` is what makes an internal/external split possible without a second round
    trip in the cases where the members are already in hand.
    """

    timeout_ms: int = 800
    verified_domains: frozenset[str] = field(default_factory=frozenset)
    tenant_id: str | None = None


@runtime_checkable
class Resolver(Protocol):
    """Resolve a symbolic target to the cardinality of the set it addresses.

    Implementations must satisfy the four rules in RESOLVER_CONTRACT.md:
    direction is declared, PARTIAL is unmergeable, no wall-clock reaches the decision, and every
    response is positively asserted before being believed.
    """

    @property
    def unit(self) -> Unit:
        """What this resolver counts.

        A read-only property rather than a plain attribute so that a `ClassVar` on an
        implementation satisfies it — a resolver's unit is fixed by what it measures and is not
        something a caller may reassign. Note the gate may still *relabel* the unit for its own
        role (principals counted as recipients); that happens in the engine, on the resolution,
        and never by mutating the resolver.
        """
        ...

    @property
    def breakdown_keys(self) -> frozenset[str]:
        """Which keys this resolver may put in `Resolution.breakdown`. Empty for most.

        Declared so a policy naming a `breakdown_bands` key nothing emits can be refused at
        construction instead of sitting there looking configured. `decide` skips a missing key by
        design — a resolver that failed to produce a breakdown must not be treated as reporting
        zero — and that correct behaviour is exactly what makes a typo invisible without this.
        """
        return frozenset()

    def resolve(self, target: str, ctx: ResolveContext) -> Resolution:
        """How big is *this* target. Never raises for provider failure — returns UNRESOLVED."""
        ...

    def reachable_max(self, ctx: ResolveContext) -> Resolution:
        """The largest magnitude this resolver could return in this tenant.

        Powers `neti inventory`: the hour-one finding that needs no traffic and no declared
        ceilings. "Your agent holds a credential that can, in one call, remove 41,203 people."
        """
        ...
