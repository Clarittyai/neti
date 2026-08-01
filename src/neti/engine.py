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
from typing import Any, Protocol

from neti.config.policy import Policy
from neti.core.budget import SessionTally, check_budgets
from neti.core.decide import decide
from neti.core.record import DecisionRecord, build_record
from neti.core.types import Ceiling, Decision, ProposedCall, Resolution
from neti.core.units import Unit
from neti.core.verdict import ResolutionState
from neti.resolvers.base import ResolveContext, Resolver

__all__ = [
    "BOUND",
    "COMPARED",
    "INTERCEPTED",
    "RESOLVED",
    "RESOLVE_STARTED",
    "SEALED",
    "Engine",
    "GateResult",
    "Observer",
]


class Observer(Protocol):
    """Called at each visible stage of `gate`, for a console that needs to show the pipeline.

    Emitted to, never consulted. This is the whole safety argument: an observer cannot influence a
    verdict, cannot fail one open, and cannot appear in a record. It exists so a UI can show what
    the engine did, not so anything can change what the engine does.
    """

    def __call__(self, stage: str, payload: dict[str, Any]) -> None: ...


def _ignore(stage: str, payload: dict[str, Any]) -> None:
    """The no-observer path. A branchless default beats an `if observe is not None` per stage."""


# Stage names are public API — a console renders them and they end up in screenshots.
INTERCEPTED = "intercepted"
BOUND = "bound"
RESOLVE_STARTED = "resolve_started"
RESOLVED = "resolved"
COMPARED = "compared"
SEALED = "sealed"


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

    last_digest: str | None = None
    """Digest of the record this engine's chain continues from.

    Public and settable because a process appending to an existing record file must continue that
    file's chain. Defaulting to `None` and starting fresh writes a record whose `prev_digest` does
    not match its predecessor, and `verify_chain` correctly calls that a break — a break caused by a
    restart rather than by tampering. Seed it with `neti.store.jsonl.chain_head(path)`.
    """

    _tallies: dict[str, SessionTally] = field(default_factory=dict, init=False)
    _policy_digest: str = field(default="", init=False)

    def __post_init__(self) -> None:
        if self.strict:
            self._check_resolvers_exist()
            self._check_breakdown_keys()
            self._check_budget_units()
        # Recomputed per decision it costs ~226us, which is most of the pure-CPU budget, and it
        # cannot change: Policy is frozen and this engine holds one.
        self._policy_digest = self.policy.digest()

    def _check_resolvers_exist(self) -> None:
        """A gate naming a resolver nobody registered is a gate that can never resolve.

        The sibling of `_check_budget_units`, and the same failure: silent dead config. `resolver:
        entra.principal` — one letter short — is caught today only when a call arrives, and then
        only if `on_unresolved` happens to be `block`. Declare `on_unresolved: allow` and the typo
        means the parameter is never gated at all, with nothing anywhere saying so.

        Refused at construction, because the operator who made the typo is at the keyboard now and
        will not be at 3am when the first call lands. The message names what *is* registered, since
        the mistake is nearly always a near-miss on a real name.
        """
        known = sorted(self.resolvers)
        problems = [
            f"{tool}{pointer}: no resolver named {spec.resolver!r}"
            for tool, toolspec in self.policy.tools.items()
            for pointer, spec in toolspec.gate.items()
            if spec.resolver not in self.resolvers
        ]
        if problems:
            raise ValueError(
                "policy names resolvers that do not exist:\n  "
                + "\n  ".join(problems)
                + f"\n\nRegistered: {', '.join(known) or 'none'}"
            )

    def _check_breakdown_keys(self) -> None:
        """A `breakdown_bands` key the bound resolver never emits is a rule that cannot fire.

        The third instance of one failure — declared config that looks live and is not — and the
        one that was live in the shipped example: `send_email/to` banded `guest: above 100 → block`
        against `entra.principals`, which emits no breakdown at all, on a fixture group with 412
        guests. It never fired once.

        `decide` skipping an absent key is correct and must stay: a resolver whose guest lookup
        failed must not be read as reporting zero guests, which would turn a failed lookup into a
        permissive verdict. That correctness is precisely what makes the typo invisible, so the
        check belongs here — at construction, where both halves are known.
        """
        problems: list[str] = []
        for tool, spec in self.policy.tools.items():
            for pointer, gate in spec.gate.items():
                resolver = self.resolvers.get(gate.resolver)
                if resolver is None:
                    continue  # already reported by _check_resolvers_exist
                emits = resolver.breakdown_keys
                for key in sorted(gate.breakdown_bands):
                    if key not in emits:
                        offer = ", ".join(sorted(emits)) or "no breakdown at all"
                        problems.append(
                            f"{tool}{pointer}: breakdown band {key!r} would never fire — "
                            f"{gate.resolver} emits {offer}"
                        )
        if problems:
            raise ValueError(
                "policy declares breakdown bands nothing produces:\n  " + "\n  ".join(problems)
            )

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

    def gate(self, call: ProposedCall, observe: Observer | None = None) -> GateResult:
        """Resolve, decide, record. The whole hot path.

        `observe` is emitted to and never consulted. Nothing an observer does can reach a verdict,
        and with no observer this method executes exactly the code it executed before it existed.

        The stages deliberately carry **no timings**. The observer timestamps its own arrivals, so
        the engine reads no clock it did not already read, and a console displays a number measured
        at the boundary it actually observes rather than a second, differently-derived figure.
        """
        emit = observe or _ignore
        emit(
            INTERCEPTED,
            {"tool": call.tool, "args": call.args, "gated": self.policy.is_gated(call.tool)},
        )

        gated: list[tuple[str, str | None, Ceiling]] = []
        resolutions: dict[str, Resolution] = {}

        for pointer, target, spec in self.policy.targets(call):
            resolver = self.resolvers.get(spec.resolver)
            emit(
                BOUND,
                {
                    "pointer": pointer,
                    "target": target,
                    "resolver": spec.resolver,
                    "registered": resolver is not None,
                },
            )
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
            emit(RESOLVE_STARTED, {"pointer": pointer, "target": target, "unit": unit.value})
            resolution = _relabel(resolver.resolve(target, self.ctx), unit)
            resolutions[pointer] = resolution
            emit(
                RESOLVED,
                {
                    "pointer": pointer,
                    "state": resolution.state.name.lower(),
                    "magnitude": resolution.magnitude,
                    "unit": resolution.unit.value,
                    "direction": resolution.direction.value,
                    "breakdown": dict(resolution.breakdown),
                    # The wire detail IS the credibility: the request that was made, what came
                    # back, and what it cost. The resolver already recorded all of it.
                    "evidence": dict(resolution.evidence),
                },
            )

        session_id = call.session_id or "anonymous"
        tally = self._tallies.get(session_id, SessionTally())

        prelim = decide(call, tuple(gated), resolutions, mode=self.policy.mode)
        budget = check_budgets(call.tool, prelim.args, tally, self.policy.session_budgets)
        final = decide(call, tuple(gated), resolutions, mode=self.policy.mode, budget=budget)
        emit(
            COMPARED,
            {
                "args": [
                    {
                        "pointer": a.pointer,
                        "verdict": a.verdict.name.lower(),
                        "rule": a.rule,
                        "magnitude": a.resolution.magnitude,
                        "ceiling": None if a.tripped is None else a.tripped.above,
                        "breaches": [
                            {
                                "source": b.source,
                                "observed": b.observed,
                                "above": b.above,
                                "verdict": b.verdict.name.lower(),
                            }
                            for b in a.breaches
                        ],
                    }
                    for a in final.args
                ],
                "budget": None
                if budget is None
                else {
                    "verdict": budget.verdict.name.lower(),
                    "running_total": budget.running_total,
                    "ceiling": None if budget.tripped is None else budget.tripped.above,
                },
                "verdict": final.verdict.name.lower(),
                "rule": final.rule,
            },
        )

        if final.proceeds:
            self._tallies[session_id] = tally.add_committed(final.args)

        record = build_record(
            final,
            decision_id=str(uuid.uuid4()),
            decided_at=datetime.now(UTC).isoformat(),
            policy_digest=self._policy_digest,
            code_version=self.code_version,
            args=call.args,
            session_id=call.session_id,
            prev_digest=self.last_digest,
        )
        self.last_digest = record.record_digest
        emit(
            SEALED,
            {
                "decision_id": record.decision_id,
                "prev_digest": record.prev_digest,
                "record_digest": record.record_digest,
                "policy_digest": record.policy_digest,
            },
        )
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
