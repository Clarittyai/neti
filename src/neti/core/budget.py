"""Declared cumulative session budgets — the mitigation for SCOPE.md NC-01.

The hole this closes: a per-call gate cannot see 4,000 individual sends, because each call resolves
to a magnitude of 1 and passes every per-call ceiling. Per-call resolution is structurally blind to
it. So the operator declares a *cumulative* ceiling per unit per session, and the running total is
compared to it the same way a single magnitude is.

This stays deterministic and stays out of anomaly-detection territory because the number is
declared, not learned. Nothing about the observed distribution reaches this code; `neti propose`
prints suggestions for a human to edit into config, and that is the only channel.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from neti.core.decide import worst_tripped_band
from neti.core.types import ArgDecision, Band, BudgetDecision, Frozen, sorted_bands
from neti.core.units import Unit
from neti.core.verdict import ResolutionState, Verdict

__all__ = ["BudgetRule", "SessionTally", "check_budgets"]


class BudgetRule(Frozen):
    """`unit` totals across `tools` within one session are compared against `bands`."""

    tools: frozenset[str]
    unit: Unit
    bands: tuple[Band, ...] = ()
    window: str = "session"

    @model_validator(mode="after")
    def _sort_and_check(self) -> BudgetRule:
        # Sorted for the same reason Ceiling sorts: so a human reading a record sees the tightest
        # applicable ceiling first. Correctness no longer depends on it — `worst_tripped_band`
        # selects by severity — but an unsorted list here was a real under-enforcement bug once, so
        # the invariant is asserted rather than assumed.
        object.__setattr__(self, "bands", sorted_bands(self.bands))
        return self

    def applies_to(self, tool: str) -> bool:
        return tool in self.tools


class SessionTally(Frozen):
    """Running totals per unit for one session.

    Immutable: `add` returns a new tally, so a decision can be computed against a candidate total
    without committing it. That matters because in `enforce` mode a blocked call must not consume
    budget — otherwise a single blocked attempt would poison the rest of the session.
    """

    totals: dict[str, int] = Field(default_factory=dict)
    calls: int = 0

    def total(self, unit: Unit) -> int:
        return self.totals.get(unit.value, 0)

    def add(self, unit: Unit, magnitude: int) -> SessionTally:
        merged = dict(self.totals)
        merged[unit.value] = merged.get(unit.value, 0) + magnitude
        return SessionTally(totals=merged, calls=self.calls + 1)

    def add_committed(self, args: tuple[ArgDecision, ...]) -> SessionTally:
        """Fold in the magnitudes of a call that actually executed."""
        tally = self
        for arg in args:
            res = arg.resolution
            if res.state is ResolutionState.RESOLVED and res.magnitude is not None:
                tally = tally.add(res.unit, res.magnitude)
        return SessionTally(totals=tally.totals, calls=self.calls + 1)


def check_budgets(
    tool: str,
    args: tuple[ArgDecision, ...],
    tally: SessionTally,
    rules: tuple[BudgetRule, ...],
) -> BudgetDecision:
    """Worst verdict across every applicable budget rule, given this call's contribution.

    An unresolved magnitude contributes nothing to the running total — deliberately. Inventing a
    contribution would make the total fiction; the per-argument `on_unresolved` verdict is what
    handles that case, and it has already fired by the time we get here.
    """
    applicable = [r for r in rules if r.applies_to(tool) and r.bands]
    if not applicable:
        return BudgetDecision()

    worst: BudgetDecision | None = None
    for rule in sorted(applicable, key=lambda r: r.unit.value):
        contribution = sum(
            arg.resolution.magnitude or 0
            for arg in args
            if arg.resolution.unit is rule.unit
            and arg.resolution.state is ResolutionState.RESOLVED
        )
        candidate_total = tally.total(rule.unit) + contribution
        band = worst_tripped_band(candidate_total, rule.bands)
        if band is None:
            continue
        decision = BudgetDecision(
            verdict=band.verdict,
            unit=rule.unit,
            running_total=candidate_total,
            tripped=band,
            rule=f"{rule.unit.value}_total>{band.above}",
        )
        if worst is None or decision.verdict > worst.verdict:
            worst = decision

    if worst is None:
        units = sorted({r.unit.value for r in applicable})
        return BudgetDecision(
            verdict=Verdict.ALLOW,
            running_total=sum(tally.total(r.unit) for r in applicable),
            rule=f"under_budget:{'+'.join(units)}",
        )
    return worst
