"""The MCP gateway: the enforcement point, and the reason installing `neti` is a URL change.

Transport-agnostic on purpose. Everything here operates on decoded JSON-RPC messages and delegates
the actual send to an `Upstream`, so the whole gateway is testable against a fake server with no
sockets involved — and so a stdio transport can be added later without touching this logic.

Three protocol decisions that matter, in descending order of how badly getting them wrong would
hurt:

**A denial is a tool result, not a JSON-RPC error.** MCP distinguishes protocol failures
(`{"error": ...}`, which the client surfaces as something broke) from tool failures
(`{"result": {"isError": true, "content": [...]}}`, which the *model* sees and can act on). A gate
that returns a protocol error converts a security decision into an outage: the agent's run dies
instead of the agent learning that its scope was too wide and retrying with a smaller one. Returning
`isError: true` is what makes a block recoverable, and it is the single most important line in this
file.

**Only `tools/call` is intercepted.** `initialize`, `tools/list`, `resources/*`, `prompts/*` and
every notification pass straight through. A gate that fails closed on unrecognised methods breaks
protocol negotiation and the whole session with it.

**Observe mode forwards unconditionally.** It computes and records a verdict and then gets out of
the way, which is what makes the install reversible and approvable in one conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from neti.approvals import ApprovalState, Approver
from neti.core.types import ProposedCall
from neti.core.verdict import Verdict
from neti.engine import Engine, GateResult
from neti.gatekeeper import Decision, Gatekeeper
from neti.store.jsonl import JsonlSink

__all__ = ["McpGateway", "Upstream", "explain_decision", "explain_denial"]

TOOLS_CALL = "tools/call"


class Upstream(Protocol):
    """The MCP server behind the gate."""

    def send(self, message: dict[str, Any], session_id: str | None) -> dict[str, Any] | None:
        """Forward a message. Returns `None` for notifications, which have no response."""
        ...


@dataclass
class McpGateway:
    engine: Engine
    upstream: Upstream
    sink: JsonlSink | None = None
    approver: Approver | None = None
    """A control plane, when there is one. Without it a `CONFIRM` stops the call — the free tier."""

    stats: dict[str, int] = field(default_factory=dict)
    """Counters for `neti report`'s header and for the observe→enforce conversion metric."""

    def handle(
        self, message: dict[str, Any], session_id: str | None = None
    ) -> dict[str, Any] | None:
        """Gate one JSON-RPC message."""
        if message.get("method") != TOOLS_CALL:
            return self.upstream.send(message, session_id)

        params = message.get("params") or {}
        tool = params.get("name")
        if not isinstance(tool, str):
            # Malformed per the MCP schema. Not our business to police the envelope — the upstream
            # server will reject it with a proper error, and inventing our own would mask theirs.
            return self.upstream.send(message, session_id)

        call = ProposedCall(
            tool=tool,
            args=params.get("arguments") or {},
            call_id=str(message.get("id")) if message.get("id") is not None else None,
            session_id=session_id,
        )

        decision = Gatekeeper(engine=self.engine, sink=self.sink, approver=self.approver).decide(
            call
        )
        self._count(decision)

        if decision.proceeds:
            return self.upstream.send(message, session_id)
        return self._denial(message, decision)

    # ------------------------------------------------------------------ internals

    def _count(self, decision: Decision) -> None:
        """Counters only — the Gatekeeper already wrote the record."""
        self._bump("decisions")
        self._bump(f"verdict.{decision.verdict.name.lower()}")
        if not decision.proceeds:
            self._bump("stopped")
        if decision.escalation.approval is not None:
            self._bump(f"approval.{decision.escalation.state}")

    def _bump(self, key: str) -> None:
        self.stats[key] = self.stats.get(key, 0) + 1

    def _denial(self, message: dict[str, Any], decision: Decision) -> dict[str, Any]:
        """A tool result the model can read and re-plan around."""
        text, payload = explain_decision(decision, self.engine.denial_payload(decision.result))

        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": text}],
                # Structured alongside the prose: the text is for the model, this is for any
                # client-side automation that wants the numbers without parsing English.
                "_meta": {"neti": payload},
            },
        }


def explain_decision(decision: Decision, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The sentence and the payload for a call that did not proceed — approvals included.

    `explain_denial` below answers "why was this too big". This answers the question one layer out:
    *what happened to this call*, which for an install with a control plane behind it may be "a
    person is looking at it" or "a person said no" rather than anything about a ceiling.

    It exists because that layer had been written three times and agreed twice. The hook and the MCP
    gateway each had their own copy, and they had already drifted — the gateway told the model to
    *retry this exact call once it is granted*, which is the whole point of naming an approval id,
    and the hook stopped at "is pending for this call" and left the agent with nothing to do about
    it. `Preflight` had no copy at all, so the three SDK adapters that reach approvals through it
    reported a pending approval as a flat "needs confirmation": no id, no indication a human had
    been asked, nothing to retry against. A paying customer on LangChain got strictly less than one
    on MCP, and the seam table could not see it because it had granted and denied rows and no
    pending row.
    """
    result = decision.result
    text = explain_denial(result, payload)
    approval = decision.escalation.approval

    if approval is not None and approval.state is ApprovalState.PENDING:
        # The half of wait-then-retry the *model* has to understand. Naming the id and asking for
        # the identical call is what makes a two-minute human decision fit inside a thirty-second
        # tool call: the retry finds the grant instead of raising a new request.
        text = (
            f"Preflight is waiting on a human: approval {approval.id} is pending for this "
            "call. Retry this exact call once it is granted, or continue without this step."
        )
    elif approval is not None and approval.state is ApprovalState.DENIED:
        who = f" by {approval.decided_by}" if approval.decided_by else ""
        text = f"Preflight denied{who}: a human reviewed this call and declined it."
    elif decision.escalation.error:
        # Never dressed up as a denial. An operator debugging this has to be able to tell "a person
        # said no" from "no person was reachable".
        text = (
            f"{text} (An approver was configured but could not be reached: "
            f"{decision.escalation.error}.)"
        )

    if approval is not None:
        payload = {**payload, "approval_id": approval.id, "approval_state": str(approval.state)}
    return text, payload


def explain_denial(result: GateResult, payload: dict[str, Any]) -> str:
    """The sentence the model reads.

    Written to cause the *right* retry. It names the ceiling and what the call actually resolved to,
    because an agent told only "denied" will either give up or retry the identical call, and neither
    is what we want. Naming the number teaches it to narrow the scope.

    It must also attribute the decision to whatever *actually* decided. A per-call ceiling and a
    cumulative session budget produce very different remedies — narrow this call, versus start a new
    session or get approval — and an early version quoted the per-argument ceiling even when the
    session budget was the thing that fired, telling the agent to shrink a call that was already
    small enough. `Decision.rule` already records which component dominated; use it.
    """
    verdict = result.decision.verdict
    unit = payload.get("unit", "items")
    resolved = payload.get("resolved")
    param = payload.get("parameter", "a parameter")
    decided_by_budget = result.decision.rule.startswith("session_budget:")

    if decided_by_budget and "session_ceiling" in payload:
        lead = (
            f"Preflight {'blocked this call' if verdict is Verdict.BLOCK else 'needs confirmation'}"
            f": this session has reached {payload['session_total']:,} {unit}, above the cumulative "
            f"ceiling of {payload['session_ceiling']:,}"
        )
        tail = (
            " The per-call scope is not the problem — the session total is. Start a new session or "
            "ask an operator to raise the budget."
        )
        return lead + "." + tail

    if resolved is None:
        reason = payload.get("reason", "the target could not be resolved")
        # The same lead as every other branch, rather than `verdict.name.lower()`. That produced
        # "Preflight confirm: …" — the one denial in the product that does not read as English, and
        # the only branch whose opening words do not say which of the two things happened. It is
        # also load-bearing: the seams that hand back a sentence and no structured payload are
        # classified by reading it, so a `CONFIRM` phrased as "confirm" was indistinguishable from
        # a block on exactly those runtimes.
        return (
            f"Preflight {'blocked this call' if verdict is Verdict.BLOCK else 'needs confirmation'}"
            f": {param} could not be sized ({reason}), so the call was not made. Supply a target "
            "this gate can resolve, or ask an operator to run it."
        )

    lead = (
        f"Preflight blocked this call: {param} resolves to {resolved:,} {unit}"
        if verdict is Verdict.BLOCK
        else f"Preflight needs confirmation: {param} resolves to {resolved:,} {unit}"
    )
    ceiling = payload.get("ceiling")
    limit = f", above the declared ceiling of {ceiling:,}" if ceiling is not None else ""
    tail = (
        " Narrow the target and try again."
        if verdict is Verdict.BLOCK
        else " An operator must approve it before it can run."
    )
    return lead + limit + "." + tail
