"""LlamaIndex tools, gated in front of execution.

    from neti.adapters.llamaindex_tools import gate_tools

    agent = FunctionAgent(tools=gate_tools(pf, [glob, delete_rows]), llm=llm)

**`call` and `acall`, both.** `FunctionTool` carries a sync and an async entry point and LlamaIndex
picks between them depending on what is running the tool — `FunctionAgent` and `AgentWorkflow` take
the async one, a direct `tool.call(...)` takes the sync one. Gating only the one the tests happened
to exercise would leave the other wide open, which is the same shape as the LangGraph defect this
package already shipped once: a seam that looks covered because the covered path is the one anybody
looked at.

**A denial is a `ToolOutput` with `is_error=True`, not an exception.** The agent reads the sentence
with the number in it and re-plans; raising would end the run, and a run that has died cannot narrow
its scope. `is_error` is load-bearing — without it the model reads a refusal as a successful call
that happened to return this text, and has no reason to change anything.

**A wrapper object rather than a patched method.** `FunctionTool` is constructed from a callable and
keeps `metadata` alongside it, so replacing `fn` would leave `metadata.fn_schema` describing the
original while the call went somewhere else. Wrapping keeps the two together, and keeps `metadata`
byte-identical: an agent must not be able to tell a gated tool from an ungated one by looking at it,
or the gate leaks into the prompt and into what the model believes it may attempt.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from neti.preflight import Preflight

__all__ = ["gate_tool", "gate_tools"]


def _refusal(name: str, message: str, arguments: dict[str, Any]) -> Any:
    from llama_index.core.tools.types import ToolOutput

    return ToolOutput(
        content=message,
        tool_name=name,
        raw_input=arguments,
        raw_output=message,
        # Without this the model reads a refusal as a successful call that returned this text.
        is_error=True,
    )


def _gated_tool_class() -> Any:
    """Built on first use so importing this module never requires llama-index."""
    from llama_index.core.tools.types import AsyncBaseTool

    class GatedTool(AsyncBaseTool):
        def __init__(self, preflight: Preflight, tool: Any) -> None:
            self._preflight = preflight
            self._tool = tool

        @property
        def metadata(self) -> Any:
            """Handed straight through. This is what the model is shown."""
            return self._tool.metadata

        def _verdict(self, arguments: dict[str, Any]) -> Any:
            from neti.adapters.claude_code import normalise_tool
            from neti.core.types import unreadable_arguments

            return self._preflight.check(
                normalise_tool(str(self.metadata.name)), unreadable_arguments(arguments)
            )

        def call(self, *args: Any, **kwargs: Any) -> Any:
            verdict = self._verdict(dict(kwargs))
            if not verdict.proceeds:
                return _refusal(str(self.metadata.name), verdict.message, dict(kwargs))
            return self._tool.call(*args, **kwargs)

        async def acall(self, *args: Any, **kwargs: Any) -> Any:
            verdict = self._verdict(dict(kwargs))
            if not verdict.proceeds:
                return _refusal(str(self.metadata.name), verdict.message, dict(kwargs))
            return await self._tool.acall(*args, **kwargs)

    return GatedTool


def gate_tool(preflight: Preflight, tool: Any) -> Any:
    """Wrap one LlamaIndex tool so its execution goes through the gate."""
    return _gated_tool_class()(preflight, tool)


def gate_tools(preflight: Preflight, tools: Sequence[Any]) -> list[Any]:
    """Every tool in a list, gated. The form an agent is usually built with."""
    return [gate_tool(preflight, tool) for tool in tools]
