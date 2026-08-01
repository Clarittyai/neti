"""The OpenAI Agents SDK, gated through its own guardrail mechanism.

Of every runtime this product integrates with, this one fits best — because the SDK already has the
concept. `ToolGuardrailFunctionOutput.reject_content(message)` hands the model a sentence *instead
of* running the tool, and the model reads it and re-plans. That is the identical contract neti
chose for MCP (`isError: true` with the number in the text) arrived at independently, and it means
a denial here is a first-class outcome rather than an exception someone has to catch.

The mapping:

    BLOCK    -> reject_content(sentence)   the tool does not run; the model sees why and can narrow
    CONFIRM  -> reject_content(sentence)   plus `needs_approval` on the tool, if you want a human
    ALLOW    -> allow()

**Not `RunHooks`.** `on_tool_start` looks like the obvious place and is a trap: it returns `None`,
so it can observe a call and can never stop one. An integration built on it would record verdicts
and gate nothing, and its tests would pass. Read against openai-agents 0.19 before a line of
this was written.

**Tool names are normalised**, the same way the Claude Code adapter does it, so one policy file
governs a tool whichever runtime it arrives through.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from neti.preflight import Preflight

if TYPE_CHECKING:  # pragma: no cover - import only for types
    from agents import ToolGuardrailFunctionOutput, ToolInputGuardrailData

__all__ = ["neti_guardrail", "verdict_for"]


def _arguments(raw: Any) -> dict[str, Any]:
    """The SDK hands arguments over as a JSON string. A malformed one is not an empty call.

    Returning `{}` would let a policy see no gated parameter and pass the call through — a parse
    failure turning into a permissive verdict. The sentinel below is unresolvable by every resolver,
    so the declared `on_unresolved` decides instead.
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {"__neti_unparseable__": str(raw)[:200]}
    return parsed if isinstance(parsed, dict) else {"__neti_unparseable__": str(raw)[:200]}


def verdict_for(preflight: Preflight, data: ToolInputGuardrailData) -> Any:
    """Gate one tool call and answer in the SDK's own vocabulary."""
    from agents import ToolGuardrailFunctionOutput

    from neti.adapters.claude_code import normalise_tool

    context = data.context
    verdict = preflight.check(
        normalise_tool(str(context.tool_name)),
        _arguments(getattr(context, "tool_arguments", None)),
        session_id=str(getattr(context, "tool_call_id", "") or "") or None,
    )
    if verdict.proceeds:
        return ToolGuardrailFunctionOutput.allow(output_info={"neti": verdict.payload})

    # `reject_content` rather than `raise_exception`: an exception ends the run, and the whole point
    # of naming the number is that the agent narrows its scope and tries again. A run that dies
    # cannot re-plan.
    return ToolGuardrailFunctionOutput.reject_content(
        message=verdict.message, output_info={"neti": verdict.payload}
    )


def neti_guardrail(preflight: Preflight) -> Any:
    """A tool input guardrail bound to a `Preflight`.

        pf = Preflight.from_config("neti.yaml")
        gate = neti_guardrail(pf)

        @function_tool(tool_input_guardrails=[gate])
        def remove_group_members(group: str) -> str: ...

    Attach it to the tools you want sized. A tool without it is ungated and out of scope, which is
    the same position `unknown_tool: allow` takes everywhere else in this product — coverage is
    something an operator declares, not something we assume.
    """
    from agents import tool_input_guardrail

    @tool_input_guardrail
    def gate(data: ToolInputGuardrailData) -> ToolGuardrailFunctionOutput:
        return verdict_for(preflight, data)

    return gate
