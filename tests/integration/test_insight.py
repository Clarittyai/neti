"""`neti report` and `neti propose`.

The behaviour under test is mostly about honesty: the report must surface the tail rather than
average it away, and propose must decline to invent a number it cannot support.
"""

from __future__ import annotations

from typing import Any

import pytest

from neti.config.policy import Policy, load_policy
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.synthetic import Group, SyntheticTenant, default_tenant
from neti.gateway.mcp import McpGateway
from neti.insight.propose import MIN_SAMPLES, format_proposals, propose
from neti.insight.report import build_report, format_report
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from neti.store.jsonl import JsonlSink, read_records
from tests.integration.test_gateway import FakeUpstream, call
from tests.integration.test_inventory import EXAMPLE

CRED = ClientCredential(tenant_id="t", client_id="c", client_secret="s")


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


def observe_traffic(
    tenant: SyntheticTenant, path: Any, sizes: list[int], tool: str = "send_email"
) -> None:
    """Run `sizes` through the gate in observe mode and record the decisions."""
    for i, size in enumerate(sizes):
        tenant.add(Group(f"g-obs-{i}", f"Observed {i}", transitive_members=size))

    policy: Policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.OBSERVE})
    with JsonlSink(path) as sink:
        client = GraphClient(CRED, transport=tenant.transport())
        try:
            gate = McpGateway(
                engine=Engine(policy=policy, resolvers=resolvers_for_client(client)),
                upstream=FakeUpstream(),
                sink=sink,
            )
            arg = "to" if tool == "send_email" else "group"
            for i in range(len(sizes)):
                gate.handle(call(tool, {arg: f"g-obs-{i}"}, call_id=i), "s")
        finally:
            client.close()


# --------------------------------------------------------------- report


def test_report_surfaces_the_tail_rather_than_averaging_it_away(
    tenant: SyntheticTenant, tmp_path: Any
) -> None:
    """The week-one slide: four calls exceeded a ceiling and nobody knew."""
    sizes = [3] * 40 + [8_900, 41_203]
    observe_traffic(tenant, tmp_path / "d.ndjson", sizes)

    summary = build_report(read_records(tmp_path / "d.ndjson"))
    assert summary.decisions == 42

    dist = summary.distributions[("send_email", "/to")]
    assert dist.n == 42
    assert dist.p50 == 3
    assert dist.maximum == 41_203
    # both outliers are over the example policy's 500-recipient block ceiling
    assert len(dist.over_ceiling) == 2

    text = format_report(summary)
    assert "41,203" in text
    assert "exceeded a declared ceiling" in text
    # observe mode: nothing was actually stopped, and the report must not imply otherwise
    assert "were stopped" not in text


def test_report_counts_unresolved_separately_from_magnitudes(
    tenant: SyntheticTenant, tmp_path: Any
) -> None:
    """An unresolvable target is not a zero-sized one, and must not drag the distribution down."""
    policy: Policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.OBSERVE})
    with JsonlSink(tmp_path / "d.ndjson") as sink:
        client = GraphClient(CRED, transport=tenant.transport())
        try:
            gate = McpGateway(
                engine=Engine(policy=policy, resolvers=resolvers_for_client(client)),
                upstream=FakeUpstream(),
                sink=sink,
            )
            gate.handle(call("send_email", {"to": "g-team"}, call_id=1), "s")
            gate.handle(call("send_email", {"to": "g-ddg"}, call_id=2), "s")
        finally:
            client.close()

    dist = build_report(read_records(tmp_path / "d.ndjson")).distributions[("send_email", "/to")]
    assert dist.magnitudes == [25]
    assert dist.unresolved == 1
    assert dist.p50 == 25, "an unresolved call must not be counted as a magnitude"


def test_report_warns_when_policies_differ_across_the_window(
    tenant: SyntheticTenant, tmp_path: Any
) -> None:
    """Distributions pooled across policy versions are not comparable, and a proposal built on
    them is fitted to a moving target."""
    path = tmp_path / "d.ndjson"
    for above in (500, 501):
        policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.OBSERVE})
        # perturb the policy so its digest changes
        policy = Policy.model_validate(policy.model_dump(mode="json") | {"version": above})
        with JsonlSink(path) as sink:
            client = GraphClient(CRED, transport=tenant.transport())
            try:
                gate = McpGateway(
                    engine=Engine(policy=policy, resolvers=resolvers_for_client(client)),
                    upstream=FakeUpstream(),
                    sink=sink,
                )
                gate.handle(call("send_email", {"to": "g-team"}, call_id=1), "s")
            finally:
                client.close()

    text = format_report(build_report(read_records(path)))
    assert "2 different policy versions" in text


def test_empty_report_says_so(tmp_path: Any) -> None:
    assert "No decisions recorded" in format_report(build_report([]))


# --------------------------------------------------------------- propose


def test_propose_declines_below_the_sample_floor(tenant: SyntheticTenant, tmp_path: Any) -> None:
    """A confidently-wrong ceiling from nine calls is worse than none: it looks configured."""
    observe_traffic(tenant, tmp_path / "d.ndjson", [5] * 9)

    proposals = propose(build_report(read_records(tmp_path / "d.ndjson")))
    assert len(proposals) == 1
    assert not proposals[0].actionable
    assert proposals[0].confirm_above is None

    text = format_proposals(proposals)
    assert "Not enough traffic" in text
    assert f"{MIN_SAMPLES} needed" in text


def test_propose_produces_pasteable_yaml_above_the_floor(
    tenant: SyntheticTenant, tmp_path: Any
) -> None:
    sizes = [3] * 95 + [110] * 5
    observe_traffic(tenant, tmp_path / "d.ndjson", sizes)

    proposals = propose(build_report(read_records(tmp_path / "d.ndjson")))
    p = proposals[0]
    assert p.actionable
    assert p.n == 100
    assert p.normal == 3, "p95 of a 95/5 split is the body, not the tail"
    assert p.confirm_above == 6  # 2x p95
    assert p.block_above == 50  # 10x p95 = 30, rounded up to a number a human would write
    assert p.would_block == 5, "the five large calls are exactly what this should catch"

    text = format_proposals(proposals)
    assert "verdict: confirm" in text
    assert "verdict: block" in text
    assert "read at decision time" in text, "the determinism caveat must be in the output"


def test_a_proposal_merges_into_the_existing_policy(tenant: SyntheticTenant, tmp_path: Any) -> None:
    """The output has to actually paste in. A suggestion that does not parse is not a suggestion.

    It is a *merge fragment*: traffic can only have been observed for an already-gated parameter,
    so the resolver binding exists upstream and the proposal only supplies bands. This performs the
    merge an operator would do by hand and checks the result loads.
    """
    import yaml

    observe_traffic(tenant, tmp_path / "d.ndjson", [4] * 60)
    text = format_proposals(propose(build_report(read_records(tmp_path / "d.ndjson"))))

    fragment_text = text.split("# existing `resolver:` line, once you are satisfied")[1]
    fragment_text = fragment_text.split("\n", 1)[1].split("Not enough")[0]
    fragment = yaml.safe_load(fragment_text)
    assert fragment["tools"]["send_email"]["gate"]["/to"]["bands"], "no bands in the fragment"

    merged = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    for tool, spec in fragment["tools"].items():
        for pointer, gate in spec["gate"].items():
            merged["tools"][tool]["gate"][pointer]["bands"] = gate["bands"]

    candidate = tmp_path / "merged.yaml"
    candidate.write_text(yaml.safe_dump(merged), encoding="utf-8")

    policy = load_policy(candidate)
    bands = policy.gate_specs("send_email")["/to"].bands
    assert [b.verdict.name for b in bands] == ["BLOCK", "CONFIRM"], "stored most-severe first"
    assert policy.gate_specs("send_email")["/to"].resolver == "entra.principals"


def test_a_proposal_catches_the_outliers_in_its_own_window(
    tenant: SyntheticTenant, tmp_path: Any
) -> None:
    """The regression that motivated anchoring on p95.

    143 calls, 140 of them tiny and three of them huge. Anchored on p99 the outliers sat inside the
    top percentile, defined normal as themselves, and produced a block ceiling of 50,000 — which
    would not have stopped the 41,203-recipient send the whole product exists to stop.
    """
    sizes = [4] * 140 + [820, 4_100, 41_203]
    observe_traffic(tenant, tmp_path / "d.ndjson", sizes)

    p = propose(build_report(read_records(tmp_path / "d.ndjson")))[0]
    assert p.block_above is not None
    assert p.block_above < 820, "the proposal cannot catch its own outliers"
    assert p.would_block == 3
    assert p.examples == [41_203, 4_100, 820]

    text = format_proposals([p])
    assert "would have blocked 3 call(s)" in text
    assert "41,203" in text


def test_a_uniform_workload_gets_ceilings_that_stop_nothing_it_has_seen(
    tenant: SyntheticTenant, tmp_path: Any
) -> None:
    """With no tail, a proposal must not invent one. Ceilings should bind only on the unseen."""
    observe_traffic(tenant, tmp_path / "d.ndjson", [3] * 100)
    p = propose(build_report(read_records(tmp_path / "d.ndjson")))[0]
    assert p.would_block == 0
    assert p.would_confirm == 0
    assert "only bind on behaviour you have not seen yet" in format_proposals([p])
