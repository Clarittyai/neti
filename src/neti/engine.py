"""The seam between policy, resolvers and the decision.

This is the only place that knows all three. `neti.core` stays pure and `neti.resolvers` stays
ignorant of policy; the engine holds them together and is where I/O meets the decision procedure.

Session state lives here too, and the rule that governs it is worth stating plainly: **budget is
consumed by calls that actually run, never by calls that were stopped.** A blocked attempt that
still spent budget would let one rejected call poison the rest of the session, which is both wrong
and the kind of thing an operator would reasonably call a bug in the gate rather than in the agent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from neti.config.policy import Policy
from neti.core.budget import SessionTally, check_budgets
from neti.core.decide import decide
from neti.core.record import DecisionRecord, build_record
from neti.core.types import Ceiling, Decision, ProposedCall, Resolution
from neti.core.units import Unit
from neti.core.verdict import ResolutionState
from neti.resolvers.base import ResolveContext, Resolver

__all__ = ["Engine", "GateResult"]


@dataclass
class GateResult:
    decision: Decision
    record: DecisionRecord

    @property
    def proceeds(self) -> bool:
        return self.decision.proceeds


@dataclass
class Engine:
    policy: Policy
    resolvers: dict[str, Resolver]
    ctx: ResolveContext = field(default_factory=ResolveContext)
    code_version: str = "0.1.0"

    strict: bool = True
    """Reject a policy whose session budgets can never fire. See `_check_budget_units`."""

    _tallies: dict[str, SessionTally] = field(default_factory=dict, init=False)
    _last_digest: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.strict:
            self._check_budget_units()

    def _check_budget_units(self) -> None:
        """A session budget in a unit no gated parameter produces is silent dead config.

        Found the hard way: `send_email` bound a `principals` resolver while the budget was declared
        in `recipients`, so the NC-01 mitigation quietly never fired. Nothing errored, nothing
        logged, and the only symptom was a gate that failed to gate. The Engine knows both halves —
        the policy's units and the resolvers' units — so it is the only place this can be caught,
        and it is caught at construction rather than on the hot path.
        """
        problems: list[str] = []
        for rule in self.policy.session_budgets:
            produced: set[Unit] = set()
            for tool in sorted(rule.tools):
                for spec in self.policy.gate_specs(tool).values():
                    resolver = self.resolvers.get(spec.resolver)
                    produced.add(spec.unit or (resolver.unit if resolver else Unit.OBJECTS))
            if rule.unit not in produced:
                have = ", ".join(sorted(u.value for u in produced)) or "nothing"
                problems.append(
                    f"session budget on {sorted(rule.tools)} counts {rule.unit.value!r}, "
                    f"but those tools' gated parameters produce {have}. "
                    f"Declare `unit: {rule.unit.value}` on the relevant gate, or change the budget."
                )
        if problems:
            raise ValueError(
                "policy has session budgets that can never fire:\n  " + "\n  ".join(problems)
            )

    def gate(self, call: ProposedCall) -> GateResult:
        """Resolve, decide, record. The whole hot path."""
        gated: list[tuple[str, str | None, Ceiling]] = []
        resolutions: dict[str, Resolution] = {}

        for pointer, target, spec in self.policy.targets(call):
            resolver = self.resolvers.get(spec.resolver)
            if resolver is None:
                # A policy naming a resolver we do not have is a misconfiguration, and the honest
                # answer is ignorance rather than silence: `decide` routes it through the declared
                # `on_unresolved`, which fails closed.
                unit = spec.unit or _any_unit(spec.resolver)
                resolutions[pointer] = Resolution.unresolved(
                    unit, reason=f"no resolver registered named {spec.resolver!r}"
                )
                gated.append((pointer, target, spec.ceiling(unit)))
                continue

            unit = spec.unit or resolver.unit
            gated.append((pointer, target, spec.ceiling(unit)))
            if target is None:
                resolutions[pointer] = Resolution.unresolved(
                    unit, reason="gated argument absent from the call"
                )
                continue
            resolutions[pointer] = _relabel(resolver.resolve(target, self.ctx), unit)

        session_id = call.session_id or "anonymous"
        tally = self._tallies.get(session_id, SessionTally())

        prelim = decide(call, tuple(gated), resolutions, mode=self.policy.mode)
        budget = check_budgets(call.tool, prelim.args, tally, self.policy.session_budgets)
        final = decide(
            call, tuple(gated), resolutions, mode=self.policy.mode, budget=budget
        )

        if final.proceeds:
            self._tallies[session_id] = tally.add_committed(final.args)

        record = build_record(
            final,
            decision_id=str(uuid.uuid4()),
            decided_at=datetime.now(UTC).isoformat(),
            policy_digest=self.policy.digest(),
            code_version=self.code_version,
            args=call.args,
            session_id=call.session_id,
            prev_digest=self._last_digest,
        )
        self._last_digest = record.record_digest
        return GateResult(decision=final, record=record)

    def denial_payload(self, result: GateResult) -> dict[str, object]:
        """What the agent gets back instead of the tool result.

        Structured and specific on purpose. There is no oracle to protect here — unlike a
        provenance gate, the verdict leaks nothing an attacker could not measure directly — and an
        agent that re-plans to a smaller scope is exactly the outcome we want.
        """
        worst = max(
            result.decision.args,
            key=lambda a: (a.verdict, a.resolution.magnitude or 0),
            default=None,
        )
        payload: dict[str, object] = {
            "error": "preflight_ceiling_exceeded",
            "verdict": result.decision.verdict.name.lower(),
            "rule": result.decision.rule,
            "decision_id": result.record.decision_id,
        }
        if worst is not None:
            payload["parameter"] = worst.pointer
            payload["unit"] = worst.resolution.unit.value
            if worst.resolution.state is ResolutionState.RESOLVED:
                payload["resolved"] = worst.resolution.magnitude
            else:
                payload["resolved"] = None
                payload["reason"] = worst.resolution.evidence.get("reason", "unresolved")
            if worst.tripped is not None:
                payload["ceiling"] = worst.tripped.above
        budget = result.decision.budget
        if budget is not None and budget.tripped is not None:
            payload["session_total"] = budget.running_total
            payload["session_ceiling"] = budget.tripped.above
        return payload

    def session_total(self, session_id: str, unit: Unit) -> int:
        tally = self._tallies.get(session_id)
        return 0 if tally is None else tally.total(unit)


def _relabel(resolution: Resolution, unit: Unit) -> Resolution:
    """Apply the gate's declared unit to a resolution, keeping the resolver's own unit as evidence.

    The same resolved number means different things in different roles: a group's transitive member
    count is *principals* when you are removing them and *recipients* when you are mailing them.
    The unit is therefore a property of the parameter's role in the policy, not of the resolver, and
    a gate may declare it.

    This is not cosmetic. Session budgets aggregate by unit, so a `recipients` budget silently never
    fires against `principals` resolutions — which is exactly how it was found. The original unit
    stays in `evidence` so the record still shows what was actually measured.
    """
    if resolution.unit is unit:
        return resolution
    return resolution.model_copy(
        update={
            "unit": unit,
            "evidence": dict(resolution.evidence) | {"resolver_unit": resolution.unit.value},
        }
    )


def _any_unit(_resolver_name: str) -> Unit:
    return Unit.OBJECTS
