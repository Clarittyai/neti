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
from neti.core.units import Direction, may_allow

__all__ = [
    "Distribution",
    "Flagged",
    "Observation",
    "ReportSummary",
    "build_report",
    "format_report",
]


@dataclass(frozen=True)
class Observation:
    """One resolved magnitude, with the direction it was measured in.

    The direction has to travel with the number. A magnitude on its own cannot answer "would this
    call have passed a ceiling of 500?", because a `LOWER_BOUND` of 3 does not clear *any* ceiling —
    `decide.py:94` escalates it to `on_unbounded` instead. `propose` used to compare bare integers
    and therefore under-reported its own interrupt rate for every resolver that reports a bound.
    """

    magnitude: int
    direction: str

    @property
    def can_clear_a_ceiling(self) -> bool:
        return may_allow(Direction(self.direction))


@dataclass(frozen=True)
class Flagged:
    """A call that destroyed something whose size nobody could read.

    The report used to say only *"3 could not be resolved"*, which reads as three harmless commands
    — and for `npm test` it is exactly right. It was also what `cat list.txt | xargs rm` looked
    like. These are pulled out by name because the whole point of `on_unsized_risk` is that the two
    stop sharing a line.
    """

    decision_id: str
    tool: str
    target: str
    reason: str
    """Why no number: `compound_command`, `target_contains_a_shell_variable`, and so on."""

    form: str
    """What gave it away as destructive: `destructive_verb:rm`, `find_delete`, `git_clean`."""

    said: str | None = None
    """What the agent called it, when the tool carries a description."""


@dataclass
class Distribution:
    """Observed magnitudes for one (tool, parameter) pair."""

    tool: str
    pointer: str
    unit: str
    observations: list[Observation] = field(default_factory=list)
    unresolved: int = 0
    unsized_risk: int = 0
    """Of the unresolved, how many destroyed something. The rest are `npm test`."""

    over_ceiling: list[tuple[str, int, int]] = field(default_factory=list)
    """`(decision_id, observed, ceiling)` for every call that breached — the tail that sells."""

    @property
    def magnitudes(self) -> list[int]:
        """The numbers alone, for percentiles and for the console. Derived rather than stored so
        that a magnitude and its direction cannot drift apart."""
        return [o.magnitude for o in self.observations]

    @property
    def unbounded(self) -> int:
        """Observations that cannot clear any ceiling, whatever ceiling is chosen."""
        return sum(1 for o in self.observations if not o.can_clear_a_ceiling)

    @property
    def n(self) -> int:
        return len(self.observations)

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

    stated: dict[str, str] = field(default_factory=dict)
    """What the agent said it was doing, by decision id.

    `Bash` and `Task` both carry a `description`, and it has been sealed inside the chained record
    all along. Kept beside the magnitudes so a breach can be printed as what it *was* next to what
    it was *called* — "clean up build artifacts", 22,794 objects.

    Recorded, never trusted. Nothing reads this to decide anything; it is here so a person reading
    a breach does not have to go and find the record themselves.
    """

    flagged: list[Flagged] = field(default_factory=list)
    """Every unmeasured deletion in the window, in the order it happened."""

    synthetic: int = 0
    """How many of these decisions came from `--demo` rather than from a provider.

    Counted and surfaced because the numbers in a synthetic record are exact, confident and
    invented, and the default records path is the one a real run writes to. A distribution built
    partly from a demo is not a distribution of anything, and `neti propose` reads exactly this
    summary to suggest ceilings — so a total that quietly blended the two would put fabricated
    traffic behind a number an operator is about to commit."""

    @property
    def ordered(self) -> list[Distribution]:
        return sorted(self.distributions.values(), key=lambda d: (-len(d.over_ceiling), -d.maximum))


def build_report(records: Iterable[DecisionRecord]) -> ReportSummary:
    summary = ReportSummary()

    for record in records:
        summary.decisions += 1
        summary.synthetic += 1 if record.synthetic else 0
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
                if cause.get("destructive"):
                    dist.unsized_risk += 1
                    said = (record.args or {}).get("description")
                    summary.flagged.append(
                        Flagged(
                            decision_id=record.decision_id,
                            tool=record.tool,
                            target=str(cause.get("target") or ""),
                            reason=str(cause.get("reason") or "unknown"),
                            form=str(cause.get("destructive") or "destructive"),
                            said=said.strip() if isinstance(said, str) and said.strip() else None,
                        )
                    )
                continue
            dist.observations.append(
                Observation(
                    magnitude=int(magnitude),
                    # Recorded on every cause since the first release; see `core/decide.py`. Absent
                    # only in hand-written fixtures, where EXACT is the reading that keeps old
                    # records meaning what they meant.
                    direction=str(cause.get("direction") or Direction.EXACT.value),
                )
            )

            # A breach is recorded whether or not it decided the call, and whether or not the mode
            # was enforcing. In observe mode that is the entire point: these are the calls that ran.
            for breach in cause.get("breaches") or []:
                if breach.get("source") == "magnitude":
                    dist.over_ceiling.append(
                        (record.decision_id, int(magnitude), int(breach["above"]))
                    )
                    said = (record.args or {}).get("description")
                    if isinstance(said, str) and said.strip():
                        summary.stated[record.decision_id] = said.strip()

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
    if summary.synthetic:
        # Loud, and above the numbers rather than under them. A reader who scrolls past this and
        # then reads a p99 has been misled by us, not by their own carelessness.
        out.append(
            f"⚠  {summary.synthetic:,} of these {summary.decisions:,} decisions are SYNTHETIC "
            "(`--demo`): magnitudes from the built-in tenant, not from any provider."
        )
        out.append(
            "   They are exact, confident and invented. Do not propose ceilings from this window — "
            "point --records at a file that only real traffic wrote."
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
            line = f"    {dist.unresolved:,} could not be resolved"
            if dist.unsized_risk:
                line += f" — {dist.unsized_risk:,} of them destroyed something (see below)"
            out.append(line)
        if dist.over_ceiling:
            worst = sorted(dist.over_ceiling, key=lambda b: -b[1])[:3]
            out.append(f"    ▸ {len(dist.over_ceiling)} call(s) exceeded a declared ceiling")
            for decision_id, observed, ceiling in worst:
                out.append(
                    f"        {observed:,} {dist.unit} against a ceiling of {ceiling:,}"
                    f"   ({decision_id[:8]})"
                )
                said = summary.stated.get(decision_id)
                if said:
                    # What the agent called it, under what it would have done. The gap between the
                    # two is the thing worth a person's attention, and it is free — the words were
                    # already in the sealed record.
                    out.append(f'            the agent said: "{said}"')

    if summary.flagged:
        # Its own section, below the distributions, because it is not a distribution: there is no
        # number to put in one. These are the calls neti watched destroy something it could not
        # measure, and a reader who only sees "could not be resolved" has been told the opposite of
        # what happened.
        out.append("")
        out.append(f"UNMEASURED DELETIONS — {len(summary.flagged):,} call(s) ran and were flagged")
        out.append("   Recognised as destructive; the size was not readable from the argument.")
        for item in summary.flagged[:10]:
            out.append("")
            out.append(f"    {item.tool}  ({item.decision_id[:8]})")
            out.append(f"        {item.target}")
            out.append(f"        {item.form}, unsizeable: {item.reason}")
            if item.said:
                out.append(f'        the agent said: "{item.said}"')
        if len(summary.flagged) > 10:
            out.append("")
            out.append(f"    … and {len(summary.flagged) - 10:,} more")

    return "\n".join(out)
