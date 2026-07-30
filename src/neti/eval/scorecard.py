"""`neti score` — the artifact that goes in a deck and, eventually, in a paper.

Two rules shape it.

**The blind spots are part of the output, not an appendix.** A scorecard that reports only what the
product catches is marketing. The incident table below is mostly misses, and it prints them first
among equals rather than in a footnote, because a security audience that finds the gap themselves
stops believing the rest of the numbers.

**Measurements that need a live tenant are listed as absent, not estimated.** M2 (latency) and M6
(time to first value) cannot be produced offline. Printing a modelled figure next to measured ones
would make the whole card unciteable, so they appear as explicitly outstanding.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from neti.config.policy import Policy
from neti.core.units import Unit
from neti.eval.incidents import Coverage, Incident, replay
from neti.insight.report import ReportSummary

__all__ = ["Scorecard", "build_scorecard", "format_scorecard", "scorecard_json"]

# Units a shipped resolver can size today. Everything else is an honest gap.
SHIPPED_UNITS = frozenset({Unit.PRINCIPALS, Unit.APPS, Unit.RECIPIENTS})

NON_COVERAGE = {
    "NC-01": "cumulative effect across calls (only declared session budgets see it)",
    "NC-02": "correctness of the action — deleting the one row that mattered",
    "NC-03": "which tool was called, in what order, or what was omitted",
    "NC-04": "whether the caller should be doing this at all (authorization is upstream)",
    "NC-05": "low-cardinality, high-consequence targets",
    "NC-06": "Exchange dynamic distribution groups (invisible to Graph)",
    "NC-07": "entitlements inside downstream apps — one hop only",
    "NC-08": "the eventual-consistency window; we sell an auditable bound, not freshness",
    "NC-09": "ungated tools and undeclared parameters",
    "NC-10": "row-count gating on SQL predicates (EXPLAIN is biased low)",
    "NC-11": "containment and rollback",
    "NC-12": "reads that are individually small but collectively large",
}


@dataclass
class Friction:
    """M5. What a policy would cost the people using it."""

    calls: int = 0
    stopped: int = 0
    confirmed: int = 0
    blocked: int = 0
    over_block_possible: int = 0

    @property
    def interrupt_rate(self) -> float:
        return 0.0 if not self.calls else self.stopped / self.calls


@dataclass
class Scorecard:
    incidents: dict[str, list[Incident]] = field(default_factory=dict)
    friction: Friction = field(default_factory=Friction)
    policy_digest: str | None = None
    gated_tools: int = 0
    gated_params: int = 0
    params_without_ceiling: int = 0
    unresolved: int = 0
    outstanding: list[str] = field(default_factory=list)

    @property
    def covered(self) -> int:
        return len(self.incidents.get(Coverage.CAUGHT.value, []))

    @property
    def total_incidents(self) -> int:
        return sum(len(v) for v in self.incidents.values())


def build_scorecard(
    summary: ReportSummary | None = None,
    policy: Policy | None = None,
    *,
    shipped_units: frozenset[Unit] = SHIPPED_UNITS,
) -> Scorecard:
    card = Scorecard(incidents=replay(shipped_units))

    card.outstanding = [
        "M1 resolution correctness — covered by the offline suite against the synthetic tenant",
        "M2 latency — REQUIRES A LIVE TENANT. Run `neti measure`. Every figure in the plan is "
        "modelled and no published Graph p50/p99 exists.",
        "M3 failure-mode matrix — covered by the offline suite",
        "M6 time to first value — REQUIRES A CLEAN MACHINE AND A TENANT. Time the install to the "
        "first `neti inventory` finding.",
        "Guest breakdown (risk R2) — UNVERIFIED. That `$filter=userType eq 'Guest'` works on the "
        "cast transitiveMembers collection with `$count` has not been confirmed against Graph.",
    ]

    if policy is not None:
        card.policy_digest = policy.digest()
        for tool in policy.tools:
            specs = policy.gate_specs(tool)
            if specs:
                card.gated_tools += 1
            for spec in specs.values():
                card.gated_params += 1
                if not spec.has_ceiling:
                    card.params_without_ceiling += 1

    if summary is not None:
        friction = Friction(calls=summary.decisions)
        friction.blocked = summary.verdicts.get("block", 0)
        friction.confirmed = summary.verdicts.get("confirm", 0)
        friction.stopped = friction.blocked + friction.confirmed
        for dist in summary.distributions.values():
            card.unresolved += dist.unresolved
        card.friction = friction

    return card


def format_scorecard(card: Scorecard) -> str:
    out: list[str] = ["neti scorecard", "=" * 72, ""]

    out.append("M4  INCIDENT REPLAY")
    out.append(
        f"    {card.covered} of {card.total_incidents} corpus entries are sized by a shipped "
        "resolver. The rest are listed because they are the questions you will be asked."
    )
    out.append("")
    labels = {
        Coverage.CAUGHT.value: "caught",
        Coverage.NEEDS_RESOLVER.value: "MISS — resolver not built",
        Coverage.NEEDS_BUDGET.value: "MISS per-call — needs a declared session budget",
        Coverage.OUT_OF_SCOPE.value: "out of scope — magnitude is the wrong primitive",
    }
    for key, label in labels.items():
        entries = card.incidents.get(key, [])
        if not entries:
            continue
        out.append(f"  [{label}]")
        for incident in entries:
            magnitude = f"{incident.magnitude:,}" if incident.magnitude is not None else "—"
            unit = incident.unit.value if incident.unit else ""
            out.append(f"    {incident.id:<24} {magnitude:>11} {unit:<11} {incident.date}")
            out.append(f"      {incident.note}")
        out.append("")

    out.append("M5  FRICTION (what the policy costs the people using it)")
    f = card.friction
    if not f.calls:
        out.append("    no recorded traffic — run observe mode, then re-run with --records")
    else:
        out.append(
            f"    {f.calls:,} calls   blocked={f.blocked:,}   confirmed={f.confirmed:,}   "
            f"interrupt rate={f.interrupt_rate:.2%}"
        )
        if card.unresolved:
            out.append(
                f"    {card.unresolved:,} parameter(s) could not be resolved — each one is a call "
                "the gate could not size, and its declared on_unresolved verdict applied"
            )
    out.append("")

    if card.policy_digest:
        out.append("POLICY")
        out.append(
            f"    digest {card.policy_digest[:16]}   {card.gated_tools} tool(s), "
            f"{card.gated_params} gated parameter(s)"
        )
        if card.params_without_ceiling:
            out.append(
                f"    {card.params_without_ceiling} parameter(s) have no ceiling declared: they "
                "resolve and record, but cannot block"
            )
        out.append("")

    out.append("KNOWN BLIND SPOTS (SCOPE.md)")
    for nc, text in NON_COVERAGE.items():
        out.append(f"    {nc}  {text}")
    out.append("")

    out.append("NOT YET MEASURED")
    for item in card.outstanding:
        out.append(f"    - {item}")
    return "\n".join(out)


def scorecard_json(card: Scorecard) -> str:
    payload: dict[str, Any] = {
        "incidents": {
            key: [asdict(i) | {"unit": i.unit.value if i.unit else None} for i in entries]
            for key, entries in card.incidents.items()
        },
        "coverage": {"caught": card.covered, "total": card.total_incidents},
        "friction": asdict(card.friction) | {"interrupt_rate": card.friction.interrupt_rate},
        "policy": {
            "digest": card.policy_digest,
            "gated_tools": card.gated_tools,
            "gated_params": card.gated_params,
            "params_without_ceiling": card.params_without_ceiling,
        },
        "unresolved_parameters": card.unresolved,
        "known_blind_spots": NON_COVERAGE,
        "not_yet_measured": card.outstanding,
    }
    return json.dumps(payload, indent=2, default=str)
