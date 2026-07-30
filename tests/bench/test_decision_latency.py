"""The latency gate, hermetic.

Two budgets, measured separately, because they fail for different reasons and only one of them is
ours to fix:

- **The decision** is pure CPU over resolutions already in hand. It should be microseconds, and if
  it ever is not, we wrote something quadratic.
- **The resolution** is a provider round trip. Against a mock transport this measures our own
  overhead — token cache, header assembly, response assertion, model construction — with the network
  removed. It is a floor, not a forecast.

**What this cannot tell you.** The real budget is dominated by Microsoft Graph, and no published
p50/p99 for it exists. `neti measure` against a live tenant is the only thing that settles whether
the synchronous 800ms design holds. This file exists so that a *regression in our own code* is
caught in CI rather than blamed on the provider.
"""

from __future__ import annotations

import time
from statistics import median

import pytest

from neti.config.policy import load_policy
from neti.core.decide import decide
from neti.core.types import Band, Ceiling, ProposedCall, Resolution
from neti.core.units import Unit
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from tests.integration.synthetic_tenant import SyntheticTenant, default_tenant
from tests.integration.test_inventory import EXAMPLE

CRED = ClientCredential(tenant_id="t", client_id="c", client_secret="s")

DECISION_BUDGET_MS = 1.0
"""Generous by three orders of magnitude against the modelled ~5us. Set as a tripwire for
algorithmic regressions, not as a performance target — a tight bound here would just be flaky."""

OVERHEAD_BUDGET_MS = 15.0
"""Our own per-resolution overhead with the network mocked out. Comfortably under the 800ms
provider budget so that a regression shows up as ours."""


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1)))]


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


def test_the_decision_itself_is_microseconds() -> None:
    """`decide` is pure and takes resolutions as input, which is what keeps it this cheap."""
    ceiling = Ceiling(
        unit=Unit.PRINCIPALS,
        bands=(Band(above=25, verdict=3), Band(above=200, verdict=2)),  # type: ignore[arg-type]
        breakdown_bands={"guest": (Band(above=100, verdict=3),)},  # type: ignore[arg-type]
    )
    call = ProposedCall(tool="remove_group_members", args={"group": "g"})
    gated = tuple((f"/p{i}", "g", ceiling) for i in range(4))
    resolutions = {
        f"/p{i}": Resolution.resolved(
            Unit.PRINCIPALS, 41_203, breakdown={"guest": 412, "internal": 40_791}
        )
        for i in range(4)
    }

    samples: list[float] = []
    for _ in range(2_000):
        start = time.perf_counter()
        decide(call, gated, resolutions, mode=Mode.ENFORCE)
        samples.append((time.perf_counter() - start) * 1000)

    p99 = percentile(samples, 0.99)
    assert p99 < DECISION_BUDGET_MS, (
        f"decision p99 {p99:.3f}ms exceeds {DECISION_BUDGET_MS}ms — something became superlinear"
    )


def test_decision_cost_is_flat_in_magnitude() -> None:
    """The claim the provider choice rests on, at the layer we control.

    A 41,203-member group must cost exactly what a 3-member group costs. If this ever fails, someone
    has made the decision depend on the size of the thing being decided about.
    """

    def timed(magnitude: int) -> float:
        ceiling = Ceiling(unit=Unit.PRINCIPALS, bands=(Band(above=200, verdict=3),))  # type: ignore[arg-type]
        call = ProposedCall(tool="t", args={})
        gated = (("/g", "g", ceiling),)
        res = {"/g": Resolution.resolved(Unit.PRINCIPALS, magnitude)}
        samples = []
        for _ in range(1_000):
            start = time.perf_counter()
            decide(call, gated, res, mode=Mode.ENFORCE)
            samples.append(time.perf_counter() - start)
        return median(samples)

    small, large = timed(3), timed(41_203)
    ratio = large / max(small, 1e-9)
    assert 0.2 < ratio < 5.0, f"decision cost scaled {ratio:.1f}x with magnitude"


def test_end_to_end_gate_overhead_excluding_the_network(tenant: SyntheticTenant) -> None:
    """Policy lookup, resolution plumbing, decision, record construction and hashing."""
    policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.ENFORCE})
    client = GraphClient(CRED, transport=tenant.transport())
    try:
        engine = Engine(policy=policy, resolvers=resolvers_for_client(client))
        engine.gate(ProposedCall(tool="send_email", args={"to": "g-team"}, session_id="warm"))

        samples: list[float] = []
        for i in range(200):
            call = ProposedCall(tool="send_email", args={"to": "g-team"}, session_id=f"s{i}")
            start = time.perf_counter()
            engine.gate(call)
            samples.append((time.perf_counter() - start) * 1000)
    finally:
        client.close()

    p99 = percentile(samples, 0.99)
    assert p99 < OVERHEAD_BUDGET_MS, (
        f"gate overhead p99 {p99:.2f}ms exceeds {OVERHEAD_BUDGET_MS}ms with the network mocked; "
        "the real budget is 800ms and the provider needs most of it"
    )


def test_gate_makes_one_provider_request_per_gated_parameter(tenant: SyntheticTenant) -> None:
    """The latency budget assumes O(1) requests. Assert it rather than trusting it."""
    policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.ENFORCE})
    client = GraphClient(CRED, transport=tenant.transport())
    try:
        engine = Engine(policy=policy, resolvers=resolvers_for_client(client))
        engine.gate(ProposedCall(tool="send_email", args={"to": "g-team"}))  # warm the token
        before = len(tenant.calls)
        engine.gate(ProposedCall(tool="remove_group_members", args={"group": "g-eng-all"}))
    finally:
        client.close()

    # two gated parameters: /group (principals) and /group#apps (app assignments)
    assert len(tenant.calls) - before == 2
