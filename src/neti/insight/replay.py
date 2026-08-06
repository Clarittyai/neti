"""Re-derive every recorded verdict from its stored evidence.

`neti verify` has always said "Replay every decision and verify the hash chain" and only ever did
the second half. Those are different claims and the gap between them is the interesting one:

- **The hash chain** proves the record has not been altered since it was written.
- **Replay** proves the verdict *follows from the evidence in the record* — that given these
  magnitudes, these directions and these declared ceilings, this decision procedure reaches this
  answer today.

The second is the claim the whole architecture is arranged around. `core/types.py` opens by saying
the split between resolving and deciding is "what makes a decision replayable: store the
resolutions, replay the decision", `core/decide.py` is pure, and `test_core_is_pure.py` enforces
that it stays pure. Every piece was in place and nothing exercised it.

What it is for, concretely: upgrade `neti`, replay a year of audit log, and find out whether any
past decision would now be decided differently. A security tool that cannot answer that is asking
to be taken on faith.

**It needs the policy**, because a record stores the resolutions but not the ceilings they were
compared against. Records written under a different policy are counted and reported rather than
silently skipped — a replay that quietly ignored half the log would be worse than none.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from neti.config.policy import Policy
from neti.core.decide import decide
from neti.core.record import DecisionRecord
from neti.core.types import Band, BudgetDecision, ProposedCall, Resolution
from neti.core.units import Direction, Unit
from neti.core.verdict import Mode, ResolutionState, Verdict

__all__ = ["Mismatch", "ReplayResult", "format_replay", "replay"]


@dataclass(frozen=True)
class Mismatch:
    decision_id: str
    tool: str
    recorded: str
    replayed: str


@dataclass
class ReplayResult:
    total: int = 0
    replayed: int = 0
    other_policy: int = 0
    """Records written under a different policy digest. Not a failure — the ceilings they were
    judged against are not the ceilings in front of us — but reported, because a replay that
    silently covered a third of the log would be a worse answer than refusing."""

    mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def _resolution(cause: dict[str, Any]) -> Resolution:
    """Rebuild what the resolver returned, from what the record kept of it."""
    unit = Unit(str(cause["unit"]))
    state = ResolutionState[str(cause["state"]).upper()]
    direction = Direction(str(cause.get("direction") or Direction.EXACT.value))
    breakdown = {str(k): int(v) for k, v in (cause.get("breakdown") or {}).items()}

    if state is not ResolutionState.RESOLVED:
        # Deliberately carries no magnitude — `Resolution` refuses one, which is the invariant that
        # stops a failed count from ever being read as a number.
        #
        # The destructive signal has to come back with it. `decide` routes an unresolved cause
        # through `on_unsized_risk` when the resolver recognised the target as destructive, so a
        # replay that dropped the signal would re-derive `allow` for every recorded `flag` and
        # report the log as inconsistent with itself. Found by running `neti verify --config` over
        # a real session rather than by reading this: two flagged calls, both "replays as allow".
        evidence: dict[str, Any] = {}
        if cause.get("destructive"):
            evidence["destructive"] = cause["destructive"]
        return Resolution(
            state=state,
            unit=unit,
            direction=direction,
            breakdown=breakdown,
            evidence=evidence,
        )

    magnitude = cause["magnitude"]
    assert magnitude is not None, "a RESOLVED cause must carry a magnitude"
    return Resolution.resolved(unit, int(magnitude), direction=direction, breakdown=breakdown)


def _budget(record: DecisionRecord) -> BudgetDecision | None:
    """The session-budget half, taken from the record rather than recomputed.

    A cumulative total depends on every call before it in that session, and the running total is not
    something a single record can reconstruct. It *is* stored, though, so replay can take it as
    given and still check the part that is a pure function: how the budget verdict joins with the
    per-argument ones.
    """
    stored = record.budget
    if stored is None:
        return None
    ceiling = stored.get("ceiling")
    unit = stored.get("unit")
    return BudgetDecision(
        verdict=Verdict[str(stored["verdict"]).upper()],
        unit=None if unit is None else Unit(str(unit)),
        running_total=int(stored.get("running_total") or 0),
        tripped=None
        if ceiling is None
        else Band(above=int(ceiling), verdict=Verdict[str(stored["verdict"]).upper()]),
        rule=str(stored.get("rule") or ""),
    )


def replay(records: Iterable[DecisionRecord], policy: Policy) -> ReplayResult:
    """Re-run `decide` over each record's stored evidence and compare the verdict."""
    digest = policy.digest()
    result = ReplayResult()

    for record in records:
        result.total += 1
        if record.policy_digest != digest:
            result.other_policy += 1
            continue

        specs = policy.gate_specs(record.tool)
        gated = []
        resolutions = {}
        for cause in record.causes:
            pointer = str(cause["pointer"])
            spec = specs.get(pointer)
            if spec is None:
                # The policy no longer gates this parameter. The digest matched, so this should be
                # impossible; treating it as a mismatch rather than skipping keeps that true.
                break
            resolution = _resolution(dict(cause))
            resolutions[pointer] = resolution
            target = cause.get("target")
            gated.append(
                (pointer, None if target is None else str(target), spec.ceiling(resolution.unit))
            )
        else:
            replayed = decide(
                ProposedCall(tool=record.tool, args=dict(record.args)),
                tuple(gated),
                resolutions,
                mode=Mode[record.mode.upper()],
                budget=_budget(record),
            )
            result.replayed += 1
            if replayed.verdict.name.lower() != record.verdict:
                result.mismatches.append(
                    Mismatch(
                        decision_id=record.decision_id,
                        tool=record.tool,
                        recorded=record.verdict,
                        replayed=replayed.verdict.name.lower(),
                    )
                )
            continue

        result.mismatches.append(
            Mismatch(
                decision_id=record.decision_id,
                tool=record.tool,
                recorded=record.verdict,
                replayed="not gated by this policy",
            )
        )

    return result


def format_replay(result: ReplayResult) -> str:
    lines: list[str] = []
    if result.mismatches:
        lines.append(f"{len(result.mismatches):,} decision(s) REPLAY DIFFERENTLY:")
        for bad in result.mismatches[:10]:
            lines.append(
                f"  {bad.decision_id[:8]}  {bad.tool}: recorded {bad.recorded}, "
                f"replays as {bad.replayed}"
            )
        if len(result.mismatches) > 10:
            lines.append(f"  … and {len(result.mismatches) - 10:,} more")
    else:
        lines.append(f"{result.replayed:,} decision(s) replay to the same verdict")

    if result.other_policy:
        lines.append(
            f"{result.other_policy:,} were decided under a different policy and were not replayed "
            "— point --config at the policy that produced them to check those too"
        )
    return "\n".join(lines)
