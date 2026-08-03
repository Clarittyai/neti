"""The Entra family against a real directory. Opt-in, and the last resolvers never run against one.

    NETI_TENANT_ID=… NETI_CLIENT_ID=… NETI_CLIENT_SECRET=… \\
    NETI_LIVE_GROUP_SMALL=<group-object-id> NETI_LIVE_GROUP_LARGE=<group-object-id> \\
    uv run pytest tests/live/test_entra_live.py -q

Four resolvers — `entra.principals`, `entra.apps`, `entra.guests`,
`entra.principals_with_guests` — are the product's wedge and the only ones with no live tier. Every
assertion about them has been made against `neti.eval.synthetic`, which is a fixture we wrote: it
reproduces the provider failures we *thought of*, which is exactly the set a fixture cannot extend.
Every defect the live tier has found elsewhere was invisible to the offline suite, including a wrong
`EXACT` from GitHub and a connection `db.rows` never closed.

**Two groups, spanning sizes, because one proves less than half.** `NETI_LIVE_GROUP_SMALL` and
`NETI_LIVE_GROUP_LARGE` are object ids from the tenant. The large one is what makes R6 — latency
flat in magnitude — a measurement rather than a single sample, and it is the claim every latency
figure in the plan rests on.

**Read-only, and it must stay that way.** `GroupMember.Read.All` is the whole permission this needs;
nothing here writes, and a tenant admin should be able to read this file and confirm that in a
minute. The gate itself only ever issues `$count` reads — that is the design, not a property of the
tests.

Skips loudly rather than passing when the credentials are absent, and `tests/live/conftest.py`
records the skip as a skip. A run without a tenant must not be able to look like a run with one:
that is the whole reason M11 stopped being a claim about filenames.
"""

from __future__ import annotations

import os
import time

import pytest

from neti.core.units import Direction, Unit
from neti.core.verdict import ResolutionState
from neti.resolvers.base import ResolveContext

TENANT = os.environ.get("NETI_TENANT_ID", "")
CLIENT = os.environ.get("NETI_CLIENT_ID", "")
SECRET = os.environ.get("NETI_CLIENT_SECRET", "")
SMALL = os.environ.get("NETI_LIVE_GROUP_SMALL", "")
LARGE = os.environ.get("NETI_LIVE_GROUP_LARGE", "")

pytestmark = pytest.mark.skipif(
    not (TENANT and CLIENT and SECRET and SMALL and LARGE),
    reason=(
        "live Entra check: set NETI_TENANT_ID, NETI_CLIENT_ID, NETI_CLIENT_SECRET and two group "
        "object ids in NETI_LIVE_GROUP_SMALL / NETI_LIVE_GROUP_LARGE"
    ),
)

CTX = ResolveContext(timeout_ms=800)
BUDGET_MS = 800


@pytest.fixture(scope="module")
def client() -> object:
    from neti.resolvers.graph_client import ClientCredential, GraphClient

    graph = GraphClient(
        ClientCredential(tenant_id=TENANT, client_id=CLIENT, client_secret=SECRET),
        timeout_ms=BUDGET_MS,
    )
    yield graph
    graph.close()


@pytest.fixture
def resolvers(client: object) -> dict[str, object]:
    from neti.resolvers.registry import resolvers_for_client

    return resolvers_for_client(client)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- the count itself


def test_a_real_group_resolves_to_a_real_number(resolvers: dict[str, object]) -> None:
    """The product's central claim, against a directory rather than a fixture."""
    out = resolvers["entra.principals"].resolve(SMALL, CTX)  # type: ignore[attr-defined]

    assert out.state is ResolutionState.RESOLVED, out.evidence
    assert out.magnitude is not None and out.magnitude >= 0
    assert out.unit is Unit.PRINCIPALS


def test_the_count_is_exact_and_says_so(resolvers: dict[str, object]) -> None:
    """`transitiveMembers/$count` is a count, not an estimate, and the direction must claim that.

    Direction is what makes a verdict sound: an `EXACT` may both allow and block, and a resolver
    that mislabels a bound as exact makes every allow above it unsound. GitHub shipped exactly that
    mistake and only the live tier found it.
    """
    from neti.core.units import may_allow, may_block

    out = resolvers["entra.principals"].resolve(LARGE, CTX)  # type: ignore[attr-defined]

    assert out.direction is Direction.EXACT
    assert may_allow(out.direction) and may_block(out.direction)


def test_nested_membership_is_included(resolvers: dict[str, object]) -> None:
    """`transitiveMembers`, not `members` — the whole reason the number surprises people.

    Asserted as a relationship rather than a figure: the transitive count of the large group cannot
    be smaller than the small group's, and the caller supplied them in that order. A tenant-specific
    number would make this file only run in one tenant.
    """
    small = resolvers["entra.principals"].resolve(SMALL, CTX)  # type: ignore[attr-defined]
    large = resolvers["entra.principals"].resolve(LARGE, CTX)  # type: ignore[attr-defined]

    assert small.magnitude is not None and large.magnitude is not None
    assert large.magnitude >= small.magnitude, (
        "NETI_LIVE_GROUP_LARGE resolved smaller than NETI_LIVE_GROUP_SMALL — the two are the wrong "
        "way round, and R6 below needs them spanning sizes to mean anything"
    )


def test_a_group_that_is_not_there_is_unresolved_and_never_zero(
    resolvers: dict[str, object],
) -> None:
    """The single most important negative in the product.

    A directory that answers "no such group" and a directory that answers "zero members" are
    opposite situations, and collapsing them would turn every unreachable target into a call that
    sails under every ceiling.
    """
    out = resolvers["entra.principals"].resolve("00000000-0000-0000-0000-000000000000", CTX)  # type: ignore[attr-defined]

    assert out.state is not ResolutionState.RESOLVED
    assert out.magnitude is None
    assert out.evidence.get("reason")


# ---------------------------------------------------------------------------- the other three


def test_app_assignments_resolve(resolvers: dict[str, object]) -> None:
    """The second unit from one target: applications lost, not people. A tenant may legitimately
    have none, so this asserts the shape rather than a floor."""
    out = resolvers["entra.apps"].resolve(LARGE, CTX)  # type: ignore[attr-defined]

    assert out.state is ResolutionState.RESOLVED, out.evidence
    assert out.unit is Unit.APPS
    assert out.magnitude is not None and out.magnitude >= 0


def test_the_guest_filter_works_on_the_cast_collection(resolvers: dict[str, object]) -> None:
    """**Risk R2**, which `neti score` has listed as UNVERIFIED since the first release.

    The claim is that `$filter=userType eq 'Guest'` works on the *cast* `transitiveMembers`
    collection together with `$count`. If Graph rejects that combination, `entra.guests` and the
    `breakdown_bands: guest` rule in `examples/entra.yaml` are a policy that can never fire — which
    is worse than an absent feature, because the operator believes it is configured.

    This is the assertion that retires R2, and it can only be made here.
    """
    out = resolvers["entra.guests"].resolve(LARGE, CTX)  # type: ignore[attr-defined]

    assert out.state is ResolutionState.RESOLVED, (
        f"R2 is REFUTED: the guest filter did not resolve against a real tenant — {out.evidence}"
    )
    assert out.magnitude is not None and out.magnitude >= 0


def test_the_breakdown_agrees_with_its_two_halves(resolvers: dict[str, object]) -> None:
    """`entra.principals_with_guests` costs two requests, and the price buys consistency.

    Its total must be the plain principal count and its guest breakdown the guest count. A
    breakdown that disagreed with the resolvers it is built from would make a `breakdown_bands` rule
    fire on a number nothing else in the product reports.
    """
    total = resolvers["entra.principals"].resolve(LARGE, CTX)  # type: ignore[attr-defined]
    guests = resolvers["entra.guests"].resolve(LARGE, CTX)  # type: ignore[attr-defined]
    both = resolvers["entra.principals_with_guests"].resolve(LARGE, CTX)  # type: ignore[attr-defined]

    assert both.magnitude == total.magnitude
    assert both.breakdown.get("guest") == guests.magnitude
    assert (both.breakdown.get("guest") or 0) <= (both.magnitude or 0)


# ---------------------------------------------------------------------------- R6 and the budget


def test_resolution_is_inside_the_budget_and_flat_in_magnitude(
    resolvers: dict[str, object],
) -> None:
    """**R6**, and the claim every latency figure in the plan rests on.

    `$count` is served from an index, so a group of forty thousand should cost what a group of forty
    costs. If it does not — if latency scales with membership — then the 800ms budget is a promise
    the product cannot keep on exactly the groups that matter most, and the whole "one hop, O(1)"
    design argument needs revisiting rather than restating.

    Three samples each after a discarded warm-up, and a deliberately loose ratio: this is a check
    that the cost is *flat*, not a benchmark, and it runs over the public internet.
    """
    principals = resolvers["entra.principals"]

    def timed(group: str, n: int = 3) -> float:
        principals.resolve(group, CTX)  # type: ignore[attr-defined]  # warm-up, discarded
        samples = []
        for _ in range(n):
            started = time.perf_counter()
            out = principals.resolve(group, CTX)  # type: ignore[attr-defined]
            samples.append((time.perf_counter() - started) * 1000)
            assert out.state is ResolutionState.RESOLVED, out.evidence
        return sorted(samples)[len(samples) // 2]

    small_ms, large_ms = timed(SMALL), timed(LARGE)

    assert large_ms < BUDGET_MS, (
        f"the large group took {large_ms:.0f}ms against a declared {BUDGET_MS}ms budget — "
        "`on_unresolved` would be deciding these calls in production"
    )
    assert large_ms < small_ms * 4 + 100, (
        f"R6 is REFUTED: {small_ms:.0f}ms for the small group and {large_ms:.0f}ms for the large "
        "one. Latency is scaling with membership, and the O(1) claim does not hold."
    )


def test_the_gate_issues_one_request_per_gated_parameter(resolvers: dict[str, object]) -> None:
    """The cost model, against the real endpoint.

    `entra.principals` is a single `$count`; `entra.principals_with_guests` is deliberately two and
    carries that in its name, so an operator opting into the guest split knows they are paying for
    it. Measured by wall clock rather than by counting requests, because the client does not expose
    a counter and the point is what the operator pays.
    """
    one = time.perf_counter()
    resolvers["entra.principals"].resolve(LARGE, CTX)  # type: ignore[attr-defined]
    single = (time.perf_counter() - one) * 1000

    two = time.perf_counter()
    resolvers["entra.principals_with_guests"].resolve(LARGE, CTX)  # type: ignore[attr-defined]
    double = (time.perf_counter() - two) * 1000

    assert double > single * 1.2, (
        "the two-request resolver was not measurably dearer than the one-request one, which means "
        "either the split is not costing what its name says or one of them is being cached"
    )
