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
from typing import Any, Protocol

from neti.approvals import Approver
from neti.config.policy import strip_mcp_prefix
from neti.core.record import DecisionRecord
from neti.core.types import ProposedCall
from neti.core.verdict import Verdict
from neti.engine import Engine
from neti.gatekeeper import Decision, Gatekeeper
from neti.gateway.mcp import explain_decision


class RecordSink(Protocol):
    def write(self, record: DecisionRecord) -> DecisionRecord: ...


__all__ = ["PRE_TOOL_USE", "hook_response", "normalise_tool", "read_event", "run_hook"]

PRE_TOOL_USE = "PreToolUse"


def normalise_tool(name: str) -> str:
    """`mcp__entra__remove_group_members` -> `remove_group_members`.

    Kept as a name here because three adapters import it, but the definition now belongs to
    `Policy` — matching a policy key is the thing it is for, and `Policy.match_tool` applies it as
    a fallback so a seam that forgets to call this still cannot let a prefixed name through.
    """
    return strip_mcp_prefix(name)


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

    # The same sentence every other seam returns. One denial, one owner — an agent must not be able
    # to tell which door it was stopped at. This used to be its own copy of the approval branches,
    # and the copy had drifted: it told the model an approval was pending and not that retrying the
    # identical call is what finds the grant.
    reason, payload = explain_decision(decision, engine.denial_payload(result))
    if decision.record_error is not None:
        # Carried in the payload so the CLI can say it out loud on stderr. Never in the sentence:
        # that is the model's to read, and "your operator's disk is full" is not something an agent
        # can act on by narrowing its scope.
        payload = {**payload, "record_error": decision.record_error}

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

    # Tell the human, if the policy asked. This is the only place a `flag` ever becomes visible at
    # the moment it happens — the call proceeds and the agent is told nothing, so without this the
    # verdict's own docstring ("recorded and surfaced") is half untrue on a real machine.
    #
    # `notify` never raises and never waits; see its module docstring. It is still called *after*
    # the decision and the record, so the ordering guarantees the gate makes are untouched by it.
    _announce(engine, decision)
    return hook_response(engine, decision)


def _announce(engine: Engine, decision: Decision) -> None:
    """Post an OS notification for this verdict, if one was declared. Best effort, always."""
    from neti.insight.notify import notify

    verdict = decision.record.verdict
    on = tuple(engine.policy.notify_on)
    if verdict not in on:
        return

    cause = decision.record.causes[0] if decision.record.causes else {}
    target = str(cause.get("target") or decision.record.tool)
    said = (decision.record.args or {}).get("description")
    detail = str(said) if isinstance(said, str) and said.strip() else str(cause.get("reason") or "")
    notify(verdict, decision.record.tool, target, detail, on=on)


def read_event(raw: str) -> dict[str, Any]:
    event: Any = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("hook input must be a JSON object")
    return event
