"""Pydantic AI, gated through its tool-execution capability hooks.

`before_tool_execute` runs after the arguments have been validated against the tool's schema and
before the tool body runs, which is precisely the seam this product wants: the arguments are already
a dict a JSON pointer can be aimed at, and nothing has happened yet.

Refusal is signalled by raising, and the exception to raise is the interesting part:

    ToolFailed(message)        -> becomes a ToolReturnPart with outcome='failed'. The run continues,
                                  the model reads the sentence, and no retry budget is consumed.
    ModelRetry(message)        -> a RetryPromptPart. Spends the retry budget and adds correction
                                  framing the gate has no business adding.
    anything else              -> ends the run.

`ToolFailed` is the exact analogue of MCP's `isError: true`: a refusal that is a tool *result*. It
is raised rather than returned, but the framework catches it internally, so the "a denial must never
kill the run" contract holds — asserted by a test that drives a real agent to completion.

Nothing is wrapped, so the tool definitions the model is shown are untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neti.preflight import Preflight

if TYPE_CHECKING:  # pragma: no cover - import only for types
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

__all__ = ["neti_hooks"]


def neti_hooks(preflight: Preflight, *, tools: list[str] | None = None) -> Any:
    """Capability hooks bound to a `Preflight`, for `Agent(capabilities=[...])`.

        pf = Preflight.from_config("neti.yaml")
        agent = Agent("anthropic:claude-opus-4-5", capabilities=[neti_hooks(pf)])

    `tools=` narrows it to named tools. Left out, every tool the agent has goes through the gate —
    and an ungated *parameter* is still out of scope rather than denied, which is where coverage is
    declared in this product (SCOPE.md NC-09).
    """
    from pydantic_ai.capabilities import Hooks
    from pydantic_ai.exceptions import ToolFailed

    from neti.adapters.claude_code import normalise_tool
    from neti.core.types import unreadable_arguments

    hooks = Hooks(id="neti")

    @hooks.on.before_tool_execute(tools=tools)
    async def gate(ctx: Any, *, call: ToolCallPart, tool_def: ToolDefinition, args: Any) -> Any:
        del ctx, tool_def
        verdict = preflight.check(
            normalise_tool(str(call.tool_name)),
            unreadable_arguments(args),
            # No `session_id`. `tool_call_id` identifies one invocation, and passing it here would
            # give every call its own budget tally — the defect the OpenAI Agents adapter shipped
            # with, which made a declared `session_budget` permanently inert on that runtime.
        )
        if verdict.proceeds:
            return args
        raise ToolFailed(verdict.message)

    return hooks
