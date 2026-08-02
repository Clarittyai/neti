"""Anthropic's agentic loop, gated where the tool actually runs.

`client.beta.messages.tool_runner(tools=[...])` runs the loop, and `BetaFunctionTool.call(input)` is
the single point where the model's request becomes a function call. Wrapping that is the whole
integration: the tool keeps its name, its schema and its docstring, and gains a gate in front.

    @beta_tool
    def remove_group_members(group: str) -> str:
        \"\"\"Remove every member of a group.\"\"\"
        ...

    runner = client.beta.messages.tool_runner(
        tools=gate_tools(pf, [remove_group_members]), ...
    )

**A denial is a return value, not an exception.** `tool_runner` feeds whatever `call` returns back
to the model as the tool result, so returning the sentence puts the number in front of the model
and it narrows its scope. Raising would abort the run, and a run that has died cannot re-plan —
the same reasoning that made `Preflight.dispatch` return the denial rather than raise.

The schema the model sees is untouched. An agent must not be able to tell a gated tool from an
ungated one by looking at it — that is what keeps the gate out of the prompt and out of the model's
reasoning about what it is allowed to attempt.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from neti.core.types import unreadable_arguments
from neti.preflight import Preflight

__all__ = ["gate_tool", "gate_tools"]


def gate_tool(preflight: Preflight, tool: Any) -> Any:
    """Wrap one `BetaFunctionTool` so its execution goes through the gate.

    Mutates `call` in place rather than building a new object. The SDK's tool objects carry a schema
    derived from the function signature, a parsed docstring and adapter state; reconstructing one
    faithfully means tracking private fields across SDK versions, and getting it subtly wrong would
    change what the model is told a tool accepts. Replacing the one method leaves everything else
    exactly as the SDK built it.
    """
    from neti.adapters.claude_code import normalise_tool

    original = tool.call
    name = normalise_tool(str(getattr(tool, "name", "") or original.__name__))

    def gated(input: object) -> Any:
        # `unreadable_arguments`, not `input if isinstance(input, dict) else {}`. Both reach the
        # same verdict — an absent gated argument and an unreadable one each resolve to `None` and
        # take the declared `on_unresolved` — which is why the two adapters could disagree here for
        # so long without a test noticing. What differed is the record: `{}` states that a call
        # arrived carrying no arguments, and an auditor reading that cannot tell it from a payload
        # the gate could not parse. The more alarming of the two was the one being erased.
        verdict = preflight.check(name, unreadable_arguments(input))
        if verdict.proceeds:
            return original(input)
        # Handed back as the tool result. The model reads the magnitude and the ceiling, which is
        # what makes it retry with a narrower target instead of giving up or repeating itself.
        return verdict.message

    tool.call = gated
    return tool


def gate_tools(preflight: Preflight, tools: Sequence[Any]) -> list[Any]:
    """Every tool in the list, gated. Pass the result straight to `tool_runner(tools=...)`."""
    return [gate_tool(preflight, tool) for tool in tools]
