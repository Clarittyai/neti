"""The Claude Code `PreToolUse` hook — the gate for agents that never speak MCP.

A large share of agent tool calls today are not MCP calls at all. They are the harness's own
built-ins, or SDK-registered functions, and no proxy sits anywhere near them. Claude Code's
`PreToolUse` hook is the one place a third party can see and stop such a call, and its contract maps
onto this product's verdict lattice almost exactly:

    BLOCK   -> deny   the call does not run; the reason goes to the model, which can re-plan
    CONFIRM -> ask    the user is prompted; a human decides
    ALLOW   -> (say nothing)

That last row is the one worth defending. The hook protocol has an explicit `allow`, and it
*bypasses the user's own permission rules* — so emitting it on every pass would silently disable the
approvals the operator already configured, in the name of a security tool. A gate that widens what
an agent may do has failed in a way no verdict of ours would ever admit to. Passing means declining
to speak, and Claude Code's normal flow continues untouched.

Tool names are normalised on the way in: Claude Code exposes MCP tools as `mcp__<server>__<tool>`,
and one policy file should govern the same tool whichever route it arrives by.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from neti.approvals import ApprovalState, Approver
from neti.core.record import DecisionRecord
from neti.core.types import ProposedCall
from neti.core.verdict import Verdict
from neti.engine import Engine
from neti.gatekeeper import Decision, Gatekeeper
from neti.gateway.mcp import explain_denial


class RecordSink(Protocol):
    def write(self, record: DecisionRecord) -> None: ...


__all__ = ["PRE_TOOL_USE", "hook_response", "normalise_tool", "read_event", "run_hook"]

PRE_TOOL_USE = "PreToolUse"

# `mcp__<server>__<tool>`, where the server name may itself contain underscores. Anchored on the
# literal `mcp__` prefix and the last `__`, which is the only part of the shape that is guaranteed.
_MCP_PREFIX = re.compile(r"^mcp__.+?__(?=[^_])")


def normalise_tool(name: str) -> str:
    """`mcp__entra__remove_group_members` -> `remove_group_members`."""
    return _MCP_PREFIX.sub("", name)


def hook_response(engine: Engine, decision: Decision) -> dict[str, Any]:
    """The hook's stdout payload. `{}` means "no opinion", which is a pass."""
    result = decision.result
    verdict = result.decision.verdict
    if verdict is Verdict.ALLOW or verdict is Verdict.FLAG:
        return {}

    # A human already said yes, so this is a pass — and a pass says nothing, which leaves the
    # operator's own permission rules to decide as they always would.
    if decision.proceeds:
        return {}

    payload = engine.denial_payload(result)
    approval = decision.escalation.approval
    if approval is not None:
        payload = {**payload, "approval_id": approval.id, "approval_state": str(approval.state)}

    if approval is not None and approval.state is ApprovalState.DENIED:
        who = f" by {approval.decided_by}" if approval.decided_by else ""
        reason = f"Preflight denied{who}: a human reviewed this call and declined it."
    elif approval is not None and approval.state is ApprovalState.PENDING:
        reason = (
            f"Preflight is waiting on a human: approval {approval.id} is pending for this call."
        )
    else:
        # The same sentence the MCP path returns. One denial, one owner — an agent should not be
        # able to tell which transport it was stopped on.
        reason = explain_denial(result, payload)

    return {
        "hookSpecificOutput": {
            "hookEventName": PRE_TOOL_USE,
            "permissionDecision": "deny" if verdict is Verdict.BLOCK else "ask",
            "permissionDecisionReason": reason,
            # The numbers alongside the prose, for anything that would rather not parse English.
            "neti": payload,
        }
    }


def run_hook(
    engine: Engine,
    event: dict[str, Any],
    sink: RecordSink | None = None,
    approver: Approver | None = None,
) -> dict[str, Any]:
    """Gate one `PreToolUse` event.

    An event this hook was not built for passes untouched. Being wired to a broader matcher than
    intended is an operator's configuration slip, and turning it into a wall of denials would be a
    worse answer than doing nothing.

    Every decision is sealed into the same chain as the MCP path, including the passes. A hook that
    only recorded its denials would leave `neti report` describing a fraction of the traffic and the
    interrupt rate reading far higher than it is.
    """
    if event.get("hook_event_name") not in (None, PRE_TOOL_USE):
        return {}

    tool = event.get("tool_name")
    if not isinstance(tool, str):
        return {}

    args = event.get("tool_input")
    call = ProposedCall(
        tool=normalise_tool(tool),
        args=args if isinstance(args, dict) else {},
        session_id=event.get("session_id"),
    )
    decision = Gatekeeper(engine=engine, sink=sink, approver=approver).decide(call)
    return hook_response(engine, decision)


def read_event(raw: str) -> dict[str, Any]:
    event: Any = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("hook input must be a JSON object")
    return event
