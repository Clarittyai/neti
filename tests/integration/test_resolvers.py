"""M1 (resolution correctness) and M3 (the failure-mode matrix).

M1 is exact rather than statistical: the synthetic tenant declares the ground truth, so a resolver
either returns the declared number or it is wrong.

M3 is the more important half. Every row asserts that a provider failure produces the *right kind of
ignorance* — and above all that nothing ever produces a plausible-looking `0`. A silent zero is the
one bug that makes this product actively dangerous: it reads as "this group is empty, nobody is
affected, allow the call".
"""

from __future__ import annotations

import httpx
import pytest

from neti.core.units import Direction, Unit
from neti.core.verdict import ResolutionState
from neti.eval.synthetic import Group, SyntheticTenant, default_tenant
from neti.resolvers.base import ResolveContext
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.graph_entra import (
    EntraAppsResolver,
    EntraGuestsResolver,
    EntraPrincipalsResolver,
    resolve_with_guest_breakdown,
)

CTX = ResolveContext(timeout_ms=800)
CRED = ClientCredential(tenant_id="t", client_id="c", client_secret="s")


def client_for(tenant: SyntheticTenant, **kw: object) -> GraphClient:
    return GraphClient(CRED, transport=tenant.transport(), **kw)  # type: ignore[arg-type]


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


# --------------------------------------------------------------- M1: correctness


@pytest.mark.parametrize(
    ("group_id", "expected"),
    [("g-solo", 1), ("g-team", 25), ("g-dept", 500), ("g-eng-all", 41_203)],
)
def test_principals_resolve_exactly(tenant: SyntheticTenant, group_id: str, expected: int) -> None:
    with client_for(tenant) as client:
        res = EntraPrincipalsResolver(client).resolve(group_id, CTX)
    assert res.state is ResolutionState.RESOLVED
    assert res.magnitude == expected
    assert res.unit is Unit.PRINCIPALS
    assert res.direction is Direction.EXACT
    assert res.consistency == "eventual"
    # No snapshot token exists for $count; claiming one would overstate reproducibility.
    assert res.provider_snapshot is None


@pytest.mark.parametrize(("group_id", "expected"), [("g-team", 3), ("g-eng-all", 37)])
def test_app_assignments_resolve_exactly(
    tenant: SyntheticTenant, group_id: str, expected: int
) -> None:
    with client_for(tenant) as client:
        res = EntraAppsResolver(client).resolve(group_id, CTX)
    assert res.state is ResolutionState.RESOLVED
    assert res.magnitude == expected
    assert res.unit is Unit.APPS


def test_one_request_per_resolution_regardless_of_group_size(tenant: SyntheticTenant) -> None:
    """The O(1) claim, at the request-count level.

    Latency flatness needs a live tenant (`neti measure`), but *request* flatness is testable here,
    and it is the mechanism behind it: a 41,203-member group must not cost more calls than a
    1-member group. If this ever fails, someone has reintroduced pagination.
    """
    with client_for(tenant) as client:
        resolver = EntraPrincipalsResolver(client)
        resolver.resolve("g-solo", CTX)
        after_token = len(tenant.calls)
        resolver.resolve("g-eng-all", CTX)
    assert len(tenant.calls) - after_token == 1


def test_guest_breakdown_splits_internal_and_external(tenant: SyntheticTenant) -> None:
    with client_for(tenant) as client:
        res = resolve_with_guest_breakdown(
            EntraPrincipalsResolver(client), EntraGuestsResolver(client), "g-eng-all", CTX
        )
    assert res.magnitude == 41_203
    assert res.breakdown == {"guest": 412, "internal": 40_791}


def test_reachable_max_is_declared_an_upper_bound(tenant: SyntheticTenant) -> None:
    """Inventory reports a bound, and the decision procedure must never allow on one."""
    with client_for(tenant) as client:
        res = EntraPrincipalsResolver(client).reachable_max(CTX)
    assert res.magnitude == 52_400
    assert res.direction is Direction.UPPER_BOUND


# --------------------------------------------------------------- M3: failure matrix


def test_dynamic_distribution_group_is_unresolved_never_zero(tenant: SyntheticTenant) -> None:
    """The single most dangerous possible bug. A DDG 404s on Graph; if that became 0, every
    `send_email` to `all-customers@` would sail through a recipient ceiling."""
    with client_for(tenant) as client:
        res = EntraPrincipalsResolver(client).resolve("g-ddg", CTX)
    assert res.state is ResolutionState.UNRESOLVED
    assert res.magnitude is None
    assert res.evidence["reason"] == "target_not_countable"
    assert "dynamic distribution" in res.evidence["hint"]


def test_deleted_group_is_unresolved(tenant: SyntheticTenant) -> None:
    with client_for(tenant) as client:
        res = EntraPrincipalsResolver(client).resolve("g-does-not-exist", CTX)
    assert res.state is ResolutionState.UNRESOLVED
    assert res.magnitude is None


@pytest.mark.parametrize("status", [401, 403])
def test_insufficient_scope_is_unresolved(tenant: SyntheticTenant, status: int) -> None:
    tenant.fail_next = [status]
    with client_for(tenant) as client:
        res = EntraPrincipalsResolver(client).resolve("g-team", CTX)
    assert res.state is ResolutionState.UNRESOLVED
    assert "GroupMember.Read.All" in res.evidence["reason"]


def test_expired_credential_is_unresolved_not_a_crash(tenant: SyntheticTenant) -> None:
    tenant.token_status = 401
    with client_for(tenant) as client:
        res = EntraPrincipalsResolver(client).resolve("g-team", CTX)
    assert res.state is ResolutionState.UNRESOLVED
    assert res.evidence["reason"].startswith("credential:")


def test_throttling_retries_once_then_succeeds(tenant: SyntheticTenant) -> None:
    tenant.fail_next = [429]
    with client_for(tenant) as client:
        res = EntraPrincipalsResolver(client).resolve("g-dept", CTX)
    assert res.state is ResolutionState.RESOLVED
    assert res.magnitude == 500
    assert res.evidence["retried_after_status"] == 429


def test_persistent_throttling_gives_up_inside_the_budget(tenant: SyntheticTenant) -> None:
    """One retry, not many. A gate that retries harder is holding the agent hostage, and a slow
    allow is worse than a fast unresolved."""
    tenant.fail_next = [429, 429, 429]
    with client_for(tenant) as client:
        res = EntraPrincipalsResolver(client).resolve("g-dept", CTX)
    assert res.state is ResolutionState.UNRESOLVED


def test_server_error_retries_once(tenant: SyntheticTenant) -> None:
    tenant.fail_next = [503]
    with client_for(tenant) as client:
        res = EntraPrincipalsResolver(client).resolve("g-dept", CTX)
    assert res.state is ResolutionState.RESOLVED


def test_json_instead_of_a_count_is_rejected(tenant: SyntheticTenant) -> None:
    """The documented silent failure: 200 OK, JSON collection, no count. Believing this response
    and defaulting to 0 is precisely the fail-open RESOLVER_CONTRACT rule 4 exists for."""
    tenant.force_json_count = True
    with client_for(tenant) as client:
        res = EntraPrincipalsResolver(client).resolve("g-eng-all", CTX)
    assert res.state is ResolutionState.UNRESOLVED
    assert res.magnitude is None
    assert "text/plain" in res.evidence["reason"]


def test_truncated_enumeration_is_never_a_number(tenant: SyntheticTenant) -> None:
    tenant.force_truncated = True
    with client_for(tenant) as client:
        res = EntraPrincipalsResolver(client).resolve("g-eng-all", CTX)
    assert res.state is not ResolutionState.RESOLVED
    assert res.magnitude is None


def test_consistency_header_is_always_sent(tenant: SyntheticTenant) -> None:
    """Without it Graph errors on /$count and silently ignores ?$count=true. The synthetic tenant
    reproduces the error, so a regression that drops the header fails loudly here."""
    with client_for(tenant) as client:
        EntraPrincipalsResolver(client).resolve("g-team", CTX)
    graph_headers = [h for h in tenant.headers_seen if "consistencylevel" in h]
    assert graph_headers, "no request carried ConsistencyLevel"
    assert all(h["consistencylevel"] == "eventual" for h in graph_headers)


def test_timeout_is_unresolved(tenant: SyntheticTenant) -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        if "token" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        raise httpx.ReadTimeout("too slow", request=request)

    with GraphClient(CRED, transport=httpx.MockTransport(timeout_handler)) as client:
        res = EntraPrincipalsResolver(client).resolve("g-team", CTX)
    assert res.state is ResolutionState.UNRESOLVED
    assert res.evidence["reason"] == "timeout"


def test_guest_lookup_failure_keeps_the_total_and_omits_the_breakdown(
    tenant: SyntheticTenant,
) -> None:
    """Risk R2's failure path. Reporting `guest: 0` would fabricate a number and turn a failed
    lookup into a permissive verdict on an external-recipient ceiling."""
    with client_for(tenant) as client:
        principals = EntraPrincipalsResolver(client)
        guests = EntraGuestsResolver(client)
        # Scope the failure to the guest call: the breakdown issues the total first, and an
        # unscoped queue would fail that instead and prove nothing.
        tenant.fail_when = "microsoft.graph.user"
        tenant.fail_next = [500, 500]  # the guest call and its one retry
        res = resolve_with_guest_breakdown(principals, guests, "g-eng-all", CTX)
    assert res.magnitude == 41_203
    assert res.breakdown == {}
    assert "unavailable" in res.evidence["guest_breakdown"]


def test_guest_count_cannot_exceed_the_total(tenant: SyntheticTenant) -> None:
    """Two eventually-consistent reads can disagree. Clamping keeps `internal` from going negative
    rather than emitting a breakdown that cannot be true."""
    tenant.add(Group("g-skew", "Skewed", transitive_members=10, guests=99))
    with client_for(tenant) as client:
        res = resolve_with_guest_breakdown(
            EntraPrincipalsResolver(client), EntraGuestsResolver(client), "g-skew", CTX
        )
    assert res.breakdown == {"guest": 10, "internal": 0}
