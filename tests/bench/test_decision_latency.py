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

import os
import time
from pathlib import Path
from statistics import median

import pytest

from neti.config.policy import load_policy
from neti.core.decide import decide
from neti.core.types import Band, Ceiling, ProposedCall, Resolution
from neti.core.units import Unit
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.synthetic import SyntheticTenant, default_tenant
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from tests.integration.test_inventory import EXAMPLE

CRED = ClientCredential(tenant_id="t", client_id="c", client_secret="s")

DECISION_BUDGET_MS = 1.0
"""Generous by three orders of magnitude against the modelled ~5us. Set as a tripwire for
algorithmic regressions, not as a performance target — a tight bound here would just be flaky."""

OVERHEAD_BUDGET_MS = 8.0
"""Our own per-resolution overhead with the network mocked out, measured at the **median**.

It was a p99 against 15ms, and it went red on `windows-latest` at 36ms while the same commit passed
on every other runner and measured **p99 0.67ms** locally — 22x under the budget it had supposedly
blown. Nothing in the gate is fifty times slower on Windows. A p99 over 200 samples is the third
slowest of the two hundred, and on a shared CI VM three scheduler stalls are not a rare event, they
are Tuesday. The assertion was measuring GitHub's hypervisor.

Widening the budget until the noise fits underneath it is the tempting fix and the wrong one: it
keeps a statistic that cannot distinguish our code from a noisy neighbour, and it raises the bar a
real regression has to clear before anyone hears about it.

So this measures the median instead, and the budget comes *down* — 8ms against a local median of
0.37ms is still 20x of headroom, and it is a strictly tighter tripwire than the 15ms p99 it
replaces. That works because of what the two statistics actually detect: an algorithmic regression —
a walk where there was a syscall, a re-read where there was a cache — makes *every* call slower and
moves the median immediately. Only the tail is noise, and the tail is the part we cannot measure on
hardware we do not own.

This repository had already worked that out once and written it down.
`tests/property/test_regressions.py::test_the_head_is_read_from_the_sidecar_rather_than_by_walking`
proves the chain head is O(1) by making the walk raise instead of by timing it, and says why in one
line: *"a timing assertion would be flaky and would prove less."* The strongest checks on the hot
path are the deterministic ones over there. This file is the weaker, noisier complement, so it
should claim the least it can get away with."""


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

    # Median, for the same reason `OVERHEAD_BUDGET_MS` uses one: the p99 of 2,000 samples is the
    # twentieth-slowest, and on a machine that is also running a browser and a Node build those
    # twenty are the scheduler, not this function. It went red here exactly that way — and passed
    # on its own a second later, which is the signature of measuring the machine.
    #
    # A regression that matters makes *every* call slower and moves the median at once. The tail is
    # the part no test on shared hardware can own.
    typical = median(samples)
    assert typical < DECISION_BUDGET_MS, (
        f"decision median {typical:.3f}ms exceeds {DECISION_BUDGET_MS}ms (p99 "
        f"{percentile(samples, 0.99):.3f}ms) — something became superlinear"
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

    # Median, not p99 — see `OVERHEAD_BUDGET_MS`. The p99 of 200 samples on a shared CI runner is
    # the third-slowest sample on somebody else's hypervisor, and that is what it was measuring.
    typical = median(samples)
    assert typical < OVERHEAD_BUDGET_MS, (
        f"gate overhead median {typical:.2f}ms exceeds {OVERHEAD_BUDGET_MS}ms with the network "
        f"mocked (p99 {percentile(samples, 0.99):.2f}ms, slowest {max(samples):.2f}ms); the real "
        "budget is 800ms and the provider needs most of it"
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


def test_the_location_check_stays_a_syscall_not_a_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """`outside_root` runs per gated argument on every call, so its cost is on the hot path.

    It is a `resolve()` — one syscall, symlinks followed — and it must stay that. The obvious
    "improvement" when somebody wants it to catch more is to walk the root, which turns a
    microsecond check into a cost that grows with the tree, on every call, for a fact that has not
    changed. This is the tripwire for that.

    **Asserted directly rather than timed.** This measured a median against `DECISION_BUDGET_MS`
    and went red on a Windows runner at 1.124ms — a 12% overshoot on a job that took 477s against a
    normal 143s. It was already using the median, so `314ad46`'s fix had been applied here and was
    not enough: that one moved p99 to median because the tail belongs to the scheduler, and this is
    a *filesystem* call whose median on a loaded shared Windows runner is simply over a millisecond.
    The budget was calibrated against the platform, not the algorithm.

    A walk is not 12% slower than a resolve, it is orders of magnitude slower — and the thing that
    makes it so is enumerating directories. So that is what gets asserted: ban every way of listing
    one and the check has to still work. A tree walk cannot survive this and no amount of load can
    fail it, which is the property a tripwire wants. The aggregate wall-clock claim still lives in
    `test_the_decision_itself_is_microseconds`, which is where an absolute budget belongs.

    This is not a new idea here, which is the part worth noticing. `OVERHEAD_BUDGET_MS` at the top
    of this file already points at
    `tests/property/test_regressions.py::test_the_head_is_read_from_the_sidecar_rather_than_by_walking`
    — *"proves the chain head is O(1) by making the walk raise instead of by timing it"* — and says
    in the same breath that a timing assertion "would be flaky and would prove less". That is
    exactly what happened to this test. The technique was written down, one test used it, and this
    one kept the stopwatch until a Windows runner made the point again.
    """
    from neti.resolvers.location import outside

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "outside() enumerated a directory. It is walking the tree rather than resolving a "
            "path, which puts a cost that grows with the repository on every gated argument."
        )

    for name in ("scandir", "listdir", "walk"):
        monkeypatch.setattr(os, name, refuse)
    for name in ("iterdir", "glob", "rglob"):
        monkeypatch.setattr(Path, name, refuse)

    # Both directions, because a walk could plausibly be introduced on either branch: one target
    # inside the root and one outside it.
    assert outside("/etc/hosts", ".") is True
    assert outside("src/neti/cli.py", ".") is False


def test_the_command_does_not_import_somebody_elses_observability_agent() -> None:
    """Pydantic's plugin scan runs on the hot path of every tool call, and we use no plugins.

    `neti hook` is one process per call, so its whole cost is import time — the decision itself is
    microseconds, measured above. Pydantic imports every plugin it finds registered in the
    environment the first time a model is built, and `logfire` registers itself as one. It arrives
    transitively with several agent stacks, which is how a real measurement on a real repository
    came out at **268ms per tool call against 120ms** on the same machine without it: 148ms of
    somebody else's observability import, per call, forever.

    Asserted on the environment variable rather than on a duration, because a timing assertion here
    would pass on any machine that happens not to have a pydantic plugin installed — which is most
    of them, including CI, and including the one where this was nearly missed.
    """
    import subprocess
    import sys
    from pathlib import Path

    # `argv[0]` is what separates the two cases, so it is what these set. A console script's argv[0]
    # is the script path; anything else — an application, a test runner — is not our process.
    def under(argv0: str) -> str:
        probe = (
            f"import sys;sys.argv[0]={argv0!r};"
            "import neti,os;print(os.environ.get('PYDANTIC_DISABLE_PLUGINS'))"
        )
        return probe

    ours = subprocess.run(
        [sys.executable, "-c", under("/x/bin/neti")],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert ours.stdout.strip() == "1", (
        f"the `neti` command still pays for the plugin scan: {ours.stdout!r} {ours.stderr[-200:]}"
    )

    theirs = subprocess.run(
        [sys.executable, "-c", under("/x/bin/pytest")],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert theirs.stdout.strip() == "None", (
        "importing neti as a library must not switch off a mechanism the host application may be "
        f"using: {theirs.stdout!r}"
    )
