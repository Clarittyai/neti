"""Entra resolvers: principals, application assignments, and the guest share.

These are the identity-blast-radius resolvers — the defensible core of the product. Nothing found in
the prior-art sweep resolves "remove group G" to the set of principals losing entitlements and gates
on it. Veza previews per-identity; Entra Agent ID and Defender give visibility, not gates.

All three are one request, `text/plain`, an integer, on the same credential
(`GroupMember.Read.All`). The apps resolver is nearly free once the principals resolver exists,
which is why the plan pairs them.

**The one-hop boundary, stated where the code is.** `appRoleAssignments/$count` tells you an app
role assignment exists; it does not tell you what that role permits. No IdP exposes the entitlement
graph inside downstream apps. So "23 people lose access to 7 applications" is resolvable and "23
people lose the ability to approve invoices" is not (SCOPE.md NC-07). Do not let a caller pretend
otherwise by relabelling the unit.
"""

from __future__ import annotations

from typing import Any, ClassVar

from neti.core.types import Resolution
from neti.core.units import Direction, Unit
from neti.core.verdict import ResolutionState
from neti.resolvers.base import ResolveContext
from neti.resolvers.graph_client import CountOutcome, GraphClient, utc_now

__all__ = ["EntraAppsResolver", "EntraGuestsResolver", "EntraPrincipalsResolver"]


class _EntraCountResolver:
    """Shared plumbing: one count read, mapped to a three-state `Resolution`.

    Direction is always EXACT. Graph's `$count` is exact against a possibly-stale index — that is a
    *freshness* caveat, carried by `consistency` and `resolved_at`, not a *bounds* caveat. Calling
    it an upper or lower bound would be wrong in both directions and would silently change the
    decision procedure's soundness branches.
    """

    unit: ClassVar[Unit]
    _path_template: ClassVar[str]
    _params: ClassVar[dict[str, str] | None] = None

    def __init__(self, client: GraphClient) -> None:
        self._client = client

    def resolve(self, target: str, ctx: ResolveContext) -> Resolution:
        outcome = self._client.count(self._path_template.format(target=target), params=self._params)
        return self._to_resolution(outcome, target)

    def _to_resolution(self, outcome: CountOutcome, target: str) -> Resolution:
        evidence: dict[str, Any] = dict(outcome.evidence)
        evidence["target"] = target

        if outcome.ok and outcome.magnitude is not None:
            return Resolution.resolved(
                self.unit,
                outcome.magnitude,
                direction=Direction.EXACT,
                resolved_at=utc_now(),
                # Graph's own documentation: the query engine indexes these properties in a separate
                # store, and "there is no way to force immediate consistency in Microsoft Graph".
                consistency="eventual",
                # No snapshot token exists for $count. Saying None is honest; inventing one would
                # make the record claim a reproducibility it does not have.
                provider_snapshot=None,
                evidence=evidence,
            )

        if outcome.status == 404:
            evidence["hint"] = (
                "not an Entra group object. Exchange dynamic distribution groups are computed by "
                "Exchange and never synced to Entra, so Graph cannot count them (SCOPE.md NC-06)."
            )
            return Resolution.unresolved(
                self.unit, reason="target_not_countable", evidence=evidence
            )

        return Resolution.unresolved(
            self.unit, reason=outcome.reason or "count_failed", evidence=evidence
        )

    def reachable_max(self, ctx: ResolveContext) -> Resolution:
        """Upper bound on what this resolver could ever return in this tenant.

        Every principal in the directory: no group can transitively contain more than that. This is
        genuinely a bound rather than a measurement, so it is declared UPPER_BOUND — which the
        decision procedure will refuse to allow on, correctly, because inventory is a reporting
        surface and must never be mistaken for a gate input.
        """
        outcome = self._client.count("/users/$count")
        if outcome.ok and outcome.magnitude is not None:
            return Resolution.resolved(
                self.unit,
                outcome.magnitude,
                direction=Direction.UPPER_BOUND,
                resolved_at=utc_now(),
                consistency="eventual",
                evidence=dict(outcome.evidence) | {"basis": "tenant user count"},
            )
        return Resolution.unresolved(
            self.unit, reason=outcome.reason or "reachable_max_failed", evidence=outcome.evidence
        )


class EntraPrincipalsResolver(_EntraCountResolver):
    """People and service identities transitively in a group.

    `GET /groups/{id}/transitiveMembers/$count` — transitive, so nested groups are included. This is
    the number that matters for `remove_group_members`: not the direct members the admin can see in
    the UI, but everyone who actually loses access.
    """

    unit = Unit.PRINCIPALS
    # One `$count`, one number. The guest split needs a second request and lives in
    # `PrincipalsWithGuestBreakdown`, so a `breakdown_bands` rule against this name is refused.
    breakdown_keys: ClassVar[frozenset[str]] = frozenset()
    _path_template = "/groups/{target}/transitiveMembers/$count"


class EntraAppsResolver(_EntraCountResolver):
    """Application assignments a group carries.

    `GET /groups/{id}/appRoleAssignments/$count`. One hop only — see the module docstring.
    """

    unit = Unit.APPS
    breakdown_keys: ClassVar[frozenset[str]] = frozenset()
    _path_template = "/groups/{target}/appRoleAssignments/$count"

    def reachable_max(self, ctx: ResolveContext) -> Resolution:
        outcome = self._client.count("/servicePrincipals/$count")
        if outcome.ok and outcome.magnitude is not None:
            return Resolution.resolved(
                self.unit,
                outcome.magnitude,
                direction=Direction.UPPER_BOUND,
                resolved_at=utc_now(),
                consistency="eventual",
                evidence=dict(outcome.evidence) | {"basis": "tenant service principal count"},
            )
        return Resolution.unresolved(
            self.unit, reason=outcome.reason or "reachable_max_failed", evidence=outcome.evidence
        )


class EntraGuestsResolver(_EntraCountResolver):
    """External (guest) members of a group, as a breakdown alongside the total.

    ⚠️ Plan risk R2: that `$filter=userType eq 'Guest'` works on the cast `transitiveMembers`
    collection together with `$count` is **unverified against a live tenant**. `neti measure` probes
    it, and until it comes back green the honest position is that the POC may ship total-count-only
    and drop `breakdown_bands`. This resolver is written so that failure is visible — the guest
    count becomes UNRESOLVED rather than silently 0, which would otherwise read as "no external
    recipients" and allow exactly the send we exist to stop.
    """

    unit = Unit.PRINCIPALS
    breakdown_keys: ClassVar[frozenset[str]] = frozenset()
    _path_template = "/groups/{target}/transitiveMembers/microsoft.graph.user/$count"
    _params: ClassVar[dict[str, str] | None] = {"$filter": "userType eq 'Guest'"}


def resolve_with_guest_breakdown(
    principals: EntraPrincipalsResolver,
    guests: EntraGuestsResolver,
    target: str,
    ctx: ResolveContext,
) -> Resolution:
    """Total principals with a `guest`/`internal` breakdown attached.

    Two requests rather than one, because Graph has no single endpoint for both. They are
    independent, so a caller that wants the latency back can issue them concurrently.

    If the total resolves but the guest split does not, the result keeps the total and simply
    carries no breakdown: a `breakdown_bands` rule on `guest` then never fires, and the operator's
    total ceiling still applies. The alternative — reporting `guest: 0` — would be a fabricated
    number that turns a failed lookup into a permissive verdict.
    """
    total = principals.resolve(target, ctx)
    if total.state is not ResolutionState.RESOLVED or total.magnitude is None:
        return total

    guest = guests.resolve(target, ctx)
    if guest.state is not ResolutionState.RESOLVED or guest.magnitude is None:
        return total.model_copy(
            update={
                "evidence": dict(total.evidence)
                | {"guest_breakdown": f"unavailable: {guest.evidence.get('reason', 'unknown')}"}
            }
        )

    guest_count = min(guest.magnitude, total.magnitude)
    return total.model_copy(
        update={
            "breakdown": {
                "guest": guest_count,
                "internal": total.magnitude - guest_count,
            }
        }
    )


class PrincipalsWithGuestBreakdown:
    """Total principals, with the external share attached as a `guest` / `internal` breakdown.

    Registered separately from `entra.principals` rather than folded into it, and the reason is the
    central latency claim. `entra.principals` is **one** O(1) `$count`; this is two, because Graph
    has no single endpoint for both. Hiding a second round trip inside the name every gate already
    uses would silently double the provider latency of every gated call — so the cost lives in the
    name, and an operator who wants the breakdown opts into paying for it.

    That split is not academic. `breakdown_bands` on `guest` was declared in the shipped example
    policy while nothing emitted a breakdown, so a `guest: above 100 → block` rule sat there looking
    configured and never fired once — on a fixture group with 412 guests. This class is the half
    that was missing; `Engine._check_breakdown_keys` is the half that stops it recurring.
    """

    unit = Unit.PRINCIPALS
    breakdown_keys: ClassVar[frozenset[str]] = frozenset({"guest", "internal"})

    def __init__(self, principals: EntraPrincipalsResolver, guests: EntraGuestsResolver) -> None:
        self._principals = principals
        self._guests = guests

    def resolve(self, target: str, ctx: ResolveContext) -> Resolution:
        return resolve_with_guest_breakdown(self._principals, self._guests, target, ctx)

    def reachable_max(self, ctx: ResolveContext) -> Resolution:
        """The total is the bound. A guest count cannot exceed it, so the total is the honest
        ceiling for both, and `neti inventory` keeps reporting one number per parameter."""
        return self._principals.reachable_max(ctx)
