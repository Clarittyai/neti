"""`neti report` — what your agents already did.

The week-one artifact, and the best first meeting available: *here are the four calls your agents
made that touched more than you would have allowed, and you did not know.* It costs the customer a
URL change and a week of observe mode.

Everything here reads recorded decisions. It never resolves anything, never calls a provider, and
never influences a verdict — it is a reporting surface over `causes`, which is exactly why the
records carry the magnitude rather than only the outcome.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from neti.core.record import DecisionRecord

__all__ = ["Distribution", "ReportSummary", "build_report", "format_report"]


@dataclass
class Distribution:
    """Observed magnitudes for one (tool, parameter) pair."""

    tool: str
    pointer: str
    unit: str
    magnitudes: list[int] = field(default_factory=list)
    unresolved: int = 0
    over_ceiling: list[tuple[str, int, int]] = field(default_factory=list)
    """`(decision_id, observed, ceiling)` for every call that breached — the tail that sells."""

    @property
    def n(self) -> int:
        return len(self.magnitudes)

    def quantile(self, q: float) -> int:
        """Nearest-rank. Reported as an integer because the underlying quantity is a count.

        Nearest-rank rather than interpolation on purpose: a p99 of "112.4 recipients" is not a
        thing, and an operator copying a proposed ceiling wants a number they can defend.
        """
        if not self.magnitudes:
            return 0
        ordered = sorted(self.magnitudes)
        rank = max(1, math.ceil(q * len(ordered)))
        return ordered[min(rank, len(ordered)) - 1]

    @property
    def p50(self) -> int:
        return self.quantile(0.50)

    @property
    def p95(self) -> int:
        return self.quantile(0.95)

    @property
    def p99(self) -> int:
        return self.quantile(0.99)

    @property
    def maximum(self) -> int:
        return max(self.magnitudes, default=0)


@dataclass
class ReportSummary:
    distributions: dict[tuple[str, str], Distribution] = field(default_factory=dict)
    decisions: int = 0
    stopped: int = 0
    verdicts: dict[str, int] = field(default_factory=dict)
    modes: set[str] = field(default_factory=set)
    policies: set[str] = field(default_factory=set)

    @property
    def ordered(self) -> list[Distribution]:
        return sorted(self.distributions.values(), key=lambda d: (-len(d.over_ceiling), -d.maximum))


def build_report(records: Iterable[DecisionRecord]) -> ReportSummary:
    summary = ReportSummary()

    for record in records:
        summary.decisions += 1
        summary.verdicts[record.verdict] = summary.verdicts.get(record.verdict, 0) + 1
        summary.modes.add(record.mode)
        summary.policies.add(record.policy_digest)
        if record.verdict in ("block", "confirm") and record.mode == "enforce":
            summary.stopped += 1

        for cause in record.causes:
            key = (record.tool, str(cause["pointer"]))
            dist = summary.distributions.get(key)
            if dist is None:
                dist = Distribution(
                    tool=record.tool, pointer=str(cause["pointer"]), unit=str(cause["unit"])
                )
                summary.distributions[key] = dist

            magnitude = cause.get("magnitude")
            if magnitude is None:
                dist.unresolved += 1
                continue
            dist.magnitudes.append(int(magnitude))

            # A breach is recorded whether or not it decided the call, and whether or not the mode
            # was enforcing. In observe mode that is the entire point: these are the calls that ran.
            for breach in cause.get("breaches") or []:
                if breach.get("source") == "magnitude":
                    dist.over_ceiling.append(
                        (record.decision_id, int(magnitude), int(breach["above"]))
                    )

    return summary


def format_report(summary: ReportSummary, *, window: str = "all recorded") -> str:
    if not summary.decisions:
        return "No decisions recorded yet. Point an MCP client at the gate and run some traffic."

    out: list[str] = []
    modes = "/".join(sorted(summary.modes))
    out.append(f"neti report — {summary.decisions:,} decisions ({window}, mode: {modes})")
    if len(summary.policies) > 1:
        # Distributions pooled across policy versions are not comparable, and a proposal built on
        # them would be fitted to a moving target.
        out.append(
            f"⚠  {len(summary.policies)} different policy versions appear in this window. "
            "Re-run over a single policy before proposing ceilings."
        )
    verdicts = "  ".join(f"{k}={v:,}" for k, v in sorted(summary.verdicts.items()))
    out.append(f"   {verdicts}")
    if summary.stopped:
        out.append(f"   {summary.stopped:,} calls were stopped")
    out.append("")

    for dist in summary.ordered:
        header = f"{dist.tool} {dist.pointer}"
        if dist.n == 0:
            out.append(f"{header}   n=0   ({dist.unresolved} unresolved)")
            continue
        out.append(
            f"{header}   n={dist.n:,}   p50={dist.p50:,}   p95={dist.p95:,}   "
            f"p99={dist.p99:,}   max={dist.maximum:,}  [{dist.unit}]"
        )
        if dist.unresolved:
            out.append(f"    {dist.unresolved:,} could not be resolved")
        if dist.over_ceiling:
            worst = sorted(dist.over_ceiling, key=lambda b: -b[1])[:3]
            out.append(f"    ▸ {len(dist.over_ceiling)} call(s) exceeded a declared ceiling")
            for decision_id, observed, ceiling in worst:
                out.append(
                    f"        {observed:,} {dist.unit} against a ceiling of {ceiling:,}"
                    f"   ({decision_id[:8]})"
                )
    return "\n".join(out)
