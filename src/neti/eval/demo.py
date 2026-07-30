"""`neti demo` — the whole narrative, produced by the real decision path.

Every number this emits comes from actually running the gate against the synthetic tenant: the
resolver resolves, `decide` decides, the record is built and hashed. Nothing is written into the
output by hand. That is the point — a demo assembled from prose drifts from the product within a
week, and the first person to check a number finds it.

The narrative runs backwards from the thing that matters. Lead with the blocked call, then show
where the ceiling came from, because "we stopped 41,203 people losing access" earns the thirty
seconds needed to explain observe mode.

**The data is synthetic and the output says so in three places.** These are not findings about a
real tenant, and presenting them as if they were is precisely the overclaim the rest of this
codebase is built to avoid.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict
from typing import Any

from neti.config.policy import Policy, load_policy
from neti.core.types import ProposedCall
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.synthetic import Group, SyntheticTenant, default_tenant
from neti.gateway.mcp import McpGateway
from neti.insight.inventory import build_inventory
from neti.insight.propose import propose
from neti.insight.report import ReportSummary, build_report
from neti.resolvers.base import ResolveContext
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client

__all__ = ["build_demo"]

_CRED = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")

DISCLAIMER = (
    "Synthetic tenant. Every number here was produced by running the real decision path against a "
    "fixture with known contents — not by observing a real directory. It demonstrates behaviour, "
    "not a finding."
)


class _Recorder:
    """Collects records instead of writing them, so the demo needs no filesystem."""

    def __init__(self) -> None:
        self.records: list[Any] = []

    def write(self, record: Any) -> None:
        self.records.append(record)


def _engine(tenant: SyntheticTenant, policy: Policy) -> tuple[Engine, GraphClient]:
    client = GraphClient(_CRED, transport=tenant.transport())
    return Engine(
        policy=policy, resolvers=resolvers_for_client(client), ctx=ResolveContext()
    ), client


def _bare(policy: Policy) -> Policy:
    """Day one: gates declared, no ceilings. Nobody has named a number yet."""
    data = policy.model_dump(mode="json")
    for spec in data["tools"].values():
        for gate in spec["gate"].values():
            gate["bands"] = []
            gate.pop("breakdown_bands", None)
    data["session_budgets"] = []
    return Policy.model_validate(data)


def _call(tool: str, args: dict[str, str], call_id: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }


def build_demo(policy_path: str) -> dict[str, Any]:
    base = load_policy(policy_path)
    tenant = default_tenant()

    # A plausible week: mostly small sends, a few large ones, one enormous.
    rng = random.Random(7)
    week = [rng.choice([1, 2, 3, 4, 6, 9]) for _ in range(140)] + [820, 4_100, 41_203]
    for i, size in enumerate(week):
        tenant.add(Group(f"w-{i}", f"Week group {i}", transitive_members=size))

    return {
        "disclaimer": DISCLAIMER,
        "act1_blocked": _act1_blocked(tenant, base),
        "act2_inventory": _act2_inventory(tenant, base),
        "act3_observe": _act3_observe(tenant, base, len(week)),
        "act4_scope": _act4_scope(),
    }


# --------------------------------------------------------------------------- act 1


def _act1_blocked(tenant: SyntheticTenant, base: Policy) -> dict[str, Any]:
    """The hook: a call that would have removed 41,203 people, stopped."""
    enforcing = base.model_copy(update={"mode": Mode.ENFORCE})
    engine, client = _engine(tenant, enforcing)
    upstream = _CountingUpstream()
    gate = McpGateway(engine=engine, upstream=upstream)
    try:
        blocked = gate.handle(_call("remove_group_members", {"group": "g-eng-all"}), "demo")
        # The contrast has to be a group that clears BOTH gated parameters. `g-team` looks like the
        # obvious choice at 25 members, but it carries 3 app assignments and the apps ceiling
        # confirms above 1 — so it is stopped too, which makes it a bad contrast rather than a good
        # one. The same call on a group of 1 with no app assignments proceeds untouched.
        allowed = gate.handle(_call("remove_group_members", {"group": "g-solo"}, 2), "demo")
        result = engine.gate(
            ProposedCall(tool="remove_group_members", args={"group": "g-eng-all"}, session_id="x")
        )
    finally:
        client.close()

    assert blocked is not None and allowed is not None
    return {
        "tool": "remove_group_members",
        "argument": "engineering-all",
        "agent_message": blocked["result"]["content"][0]["text"],
        "meta": blocked["result"]["_meta"]["neti"],
        "reached_the_server": upstream.tools_called,
        "causes": [_cause(c) for c in result.record.causes],
        "record": {
            "decision_id": result.record.decision_id,
            "verdict": result.record.verdict,
            "rule": result.record.rule,
            "policy_digest": result.record.policy_digest,
            "record_digest": result.record.record_digest,
        },
        "contrast": {
            "argument": "founders",
            "members": 1,
            "verdict": "allow",
            "reached_the_server": "remove_group_members" in upstream.tools_called,
        },
    }


class _CountingUpstream:
    def __init__(self) -> None:
        self.tools_called: list[str] = []

    def send(self, message: dict[str, Any], session_id: str | None) -> dict[str, Any] | None:
        if message.get("method") == "tools/call":
            self.tools_called.append(message["params"]["name"])
        if "id" not in message:
            return None
        return {"jsonrpc": "2.0", "id": message["id"], "result": {"content": []}}


def _cause(cause: dict[str, Any]) -> dict[str, Any]:
    return {
        "pointer": cause["pointer"],
        "unit": cause["unit"],
        "magnitude": cause["magnitude"],
        "ceiling": cause["ceiling"],
        "verdict": cause["verdict"],
        "direction": cause["direction"],
        "consistency": cause["consistency"],
        "breakdown": cause["breakdown"],
    }


# --------------------------------------------------------------------------- act 2


def _act2_inventory(tenant: SyntheticTenant, base: Policy) -> dict[str, Any]:
    """Hour one: what could each tool reach, with no traffic and no ceilings declared."""
    engine, client = _engine(tenant, _bare(base))
    try:
        rows = build_inventory(_bare(base), engine.resolvers, ResolveContext())
    finally:
        client.close()

    return {
        "rows": [
            {
                "tool": r.tool,
                "param": r.pointer,
                "resolver": r.resolver,
                "reachable": r.reachable.magnitude,
                "unit": r.reachable.unit.value,
                "has_ceiling": r.has_ceiling,
                "risk": r.risk,
            }
            for r in sorted(rows, key=lambda r: -(r.reachable.magnitude or 0))
        ],
        "caveat": (
            "Reachable maxima are upper bounds on tenant capability, not measurements of any call. "
            "They are reporting only and never gate a decision."
        ),
    }


# --------------------------------------------------------------------------- act 3


def _act3_observe(tenant: SyntheticTenant, base: Policy, week_size: int) -> dict[str, Any]:
    """A week in observe mode, then the report and the proposal that come out of it."""
    engine, client = _engine(tenant, _bare(base))
    sink = _Recorder()
    gate = McpGateway(engine=engine, upstream=_CountingUpstream(), sink=sink)  # type: ignore[arg-type]
    try:
        for i in range(week_size):
            gate.handle(_call("send_email", {"to": f"w-{i}"}, i), "week-1")
    finally:
        client.close()

    summary: ReportSummary = build_report(sink.records)
    dist = summary.distributions[("send_email", "/to")]
    proposal = propose(summary)[0]

    return {
        "decisions": summary.decisions,
        "blocked_anything": False,
        "distribution": {
            "n": dist.n,
            "p50": dist.p50,
            "p95": dist.p95,
            "p99": dist.p99,
            "max": dist.maximum,
            "unit": dist.unit,
        },
        "proposal": {
            "confirm_above": proposal.confirm_above,
            "block_above": proposal.block_above,
            "rationale": proposal.rationale,
            "would_block": proposal.would_block,
            "would_confirm": proposal.would_confirm,
            "examples": proposal.examples,
        },
        "determinism_note": (
            "The proposal is text a human edits into config. Nothing computed here is ever read at "
            "decision time — the gate only compares against numbers a person committed."
        ),
    }


# --------------------------------------------------------------------------- act 4


def _act4_scope() -> dict[str, Any]:
    """What it does not do. Kept in the demo on purpose."""
    from neti.eval.incidents import INCIDENTS, Coverage
    from neti.eval.scorecard import NON_COVERAGE, SHIPPED_UNITS, build_scorecard

    card = build_scorecard(shipped_units=SHIPPED_UNITS)
    return {
        "caught": card.covered,
        "total": card.total_incidents,
        "incidents": [
            {
                "id": i.id,
                "date": i.date,
                "actor": i.actor,
                "magnitude": i.magnitude,
                "unit": i.unit.value if i.unit else None,
                "gated_unit": i.gated_unit.value if i.gated_unit else None,
                "coverage": i.coverage.value,
                "caught": i.coverage is Coverage.CAUGHT
                and (i.sizing_unit in SHIPPED_UNITS if i.sizing_unit else False),
                "note": i.note,
                "source": i.source,
            }
            for i in INCIDENTS
        ],
        "blind_spots": NON_COVERAGE,
        "unmeasured": card.outstanding,
    }


def demo_json(policy_path: str) -> str:
    return json.dumps(build_demo(policy_path), indent=2, default=lambda o: asdict(o))
