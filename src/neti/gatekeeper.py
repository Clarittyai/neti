"""One place where a call is gated, recorded, and — if a human is required — escalated.

Every seam was doing the same three things in its own words. `McpGateway.handle` gated, wrote the
record and rendered a denial; `claude_code.run_hook` gated, wrote the record and rendered a hook
response; `Preflight.check` gated, wrote the record and rendered a `Verdict`. Three copies of one
flow is survivable until a fourth step joins it, and approvals is that step — three copies of an
escalation protocol would drift, and the one that drifted would be the one that let a call through.

So the middle is here and the edges keep their own rendering. The seams still differ in the only way
they should: what a denial *looks like* to their particular caller.

**`Engine` is untouched.** Approval is I/O — a network call and a human — and the engine's freedom
from both is what lets a stored decision be replayed and get the same answer. The escalation happens
strictly after `Engine.gate` has returned, on the result, in a layer that is allowed to block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from neti.approvals import Approval, ApprovalState, Approver, ApproverError, request_digest
from neti.core.record import DecisionRecord
from neti.core.types import ProposedCall
from neti.core.verdict import ResolutionState, Verdict
from neti.engine import Engine, GateResult

__all__ = ["Decision", "Escalation", "Gatekeeper", "RecordSink"]


class RecordSink(Protocol):
    """Structural, so `JsonlSink` satisfies it without knowing this module exists."""

    def write(self, record: DecisionRecord) -> None: ...


@dataclass(frozen=True)
class Escalation:
    """What happened when the gate asked a human, if it asked one at all."""

    approval: Approval | None = None
    error: str | None = None
    """Set when nobody could be asked. Distinct from a denial on purpose — an operator debugging a
    stopped call has to be able to tell "a person said no" from "no person was reachable"."""

    @property
    def asked(self) -> bool:
        return self.approval is not None or self.error is not None

    @property
    def state(self) -> ApprovalState | None:
        return self.approval.state if self.approval else None


@dataclass
class Decision:
    """A gated call, plus whatever the escalation added to it."""

    result: GateResult
    escalation: Escalation = field(default_factory=Escalation)

    @property
    def proceeds(self) -> bool:
        """Whether the upstream call is made.

        A granted approval is the only thing that can turn a non-proceeding verdict into a
        proceeding one, and it can only do so for the exact call it was bound to.
        """
        if self.result.proceeds:
            return True
        approval = self.escalation.approval
        return approval is not None and approval.proceeds

    @property
    def verdict(self) -> Verdict:
        return self.result.decision.verdict

    @property
    def record(self) -> DecisionRecord:
        return self.result.record


@dataclass
class Gatekeeper:
    """Gate a call, record it, and escalate a `CONFIRM` to a human if one is reachable."""

    engine: Engine
    sink: RecordSink | None = None
    approver: Approver | None = None
    """`None` is the free tier: no control plane, no escalation, behaviour exactly as before."""

    def decide(self, call: ProposedCall, **kw: Any) -> Decision:
        result = self.engine.gate(call, **kw)
        escalation = self._escalate(call, result)
        if self.sink is not None:
            self.sink.write(result.record)
        return Decision(result=result, escalation=escalation)

    # ------------------------------------------------------------------ internals

    def _escalate(self, call: ProposedCall, result: GateResult) -> Escalation:
        """Ask a human, but only when asking one is what the verdict meant.

        `BLOCK` is never escalated. A ceiling that says *stop* is not a request for a second
        opinion, and letting an approver override it would quietly turn every declared block into a
        prompt — which is exactly the erosion that makes teams stop trusting a gate.
        """
        if result.proceeds or result.decision.verdict is not Verdict.CONFIRM:
            return Escalation()

        if self.approver is None:
            return Escalation()

        digest = request_digest(call, self.engine.policy.digest())
        magnitude = _worst_magnitude(result)

        try:
            # A retry of a call that was already approved must not raise a second request. This is
            # the other half of the wait-then-retry protocol, and without it every retry would
            # summon a fresh notification to the same reviewer.
            existing = self.approver.find(digest)
            approval = existing or self.approver.request(
                call, digest, self._evidence(result, magnitude)
            )
            if approval.state is ApprovalState.GRANTED:
                approval = self.approver.redeem(approval, magnitude)
            return Escalation(approval=approval)
        except ApproverError as exc:
            return Escalation(error=str(exc))

    def _evidence(self, result: GateResult, magnitude: int | None) -> dict[str, Any]:
        """What the reviewer sees. The magnitude is the whole point of showing them anything."""
        payload = self.engine.denial_payload(result)
        return {
            "decision_id": result.record.decision_id,
            "tool": result.decision.tool,
            "verdict": result.decision.verdict.name.lower(),
            "rule": result.decision.rule,
            "magnitude": magnitude,
            "unit": payload.get("unit"),
            "ceiling": payload.get("ceiling"),
            "policy_digest": result.record.policy_digest,
            "record_digest": result.record.record_digest,
        }


def _worst_magnitude(result: GateResult) -> int | None:
    """The largest resolved magnitude in the decision, which is the number the human is judging.

    `None` when nothing resolved — an unsizeable target. A grant over an unknown magnitude cannot be
    bounded by one, so the server is told `None` and treats it as the strictest case rather than as
    zero.
    """
    resolved = [
        arg.resolution.magnitude
        for arg in result.decision.args
        if arg.resolution.state is ResolutionState.RESOLVED and arg.resolution.magnitude is not None
    ]
    return max(resolved) if resolved else None
