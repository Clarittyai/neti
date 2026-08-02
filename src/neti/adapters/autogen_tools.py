"""AutoGen, gated by wrapping the workbench — because there is no callback to use.

Of the runtimes this product adapts to, this is the only one with no before-tool hook of any kind.
`AssistantAgent._execute_tool_call` goes straight from `json.loads(tool_call.arguments)` to
`workbench.call_tool(...)`, and `autogen_core.InterventionHandler` — the thing that looks like the
answer — operates on the actor runtime's *message* layer and never sees a tool call. So this is a
wrapper, in the shape of the LangChain and Anthropic adapters rather than the OpenAI Agents one.

The workbench rather than the individual tools, for the same reason the ADK adapter uses the plugin
rather than the per-agent callback: it is one place instead of one place per tool, so coverage does
not depend on remembering. `list_tools` delegates untouched, so the schemas the model is shown are
exactly what the author registered.

A denial is a `ToolResult` with `is_error=True`, which reaches the model as a
`FunctionExecutionResult` carrying the sentence. Measured rather than assumed: returning the string
as a normal result yields `is_error=False`, and the model then reads a refusal as though the tool
had succeeded and returned that text.

**Streaming.** `_execute_tool_call` branches on `isinstance(workbench, StaticStreamWorkbench)` to
use `call_tool_stream`, so wrapping a streaming workbench in a plain `Workbench` would silently drop
tool-call streaming events. `gate_workbench` preserves the streaming path when the wrapped workbench
has one — a gate that quietly degrades the runtime it sits in front of is a gate people remove.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neti.preflight import Preflight

if TYPE_CHECKING:  # pragma: no cover - import only for types
    from autogen_core import CancellationToken
    from autogen_core.tools import ToolResult, Workbench

__all__ = ["gate_workbench"]


def _refusal(name: str, message: str) -> ToolResult:
    from autogen_core.tools import TextResultContent, ToolResult

    refusal: ToolResult = ToolResult(
        name=name,
        result=[TextResultContent(content=message)],
        # The load-bearing flag. Without it the model reads a refusal as a successful call that
        # happened to return this text, and has no reason to narrow anything.
        is_error=True,
    )
    return refusal


def gate_workbench(preflight: Preflight, workbench: Workbench) -> Workbench:
    """Wrap a workbench so every tool call through it is sized first.

        pf = Preflight.from_config("neti.yaml")
        agent = AssistantAgent("ops", model_client=client, workbench=gate_workbench(pf, bench))

    Everything other than `call_tool` delegates, including `list_tools` — so nothing about the tools
    the model is offered changes, which is what keeps a gated tool indistinguishable from an ungated
    one.
    """
    from autogen_core.tools import Workbench

    from neti.adapters.claude_code import normalise_tool
    from neti.core.types import unreadable_arguments

    streams = hasattr(workbench, "call_tool_stream")

    class _GatedWorkbench(Workbench):
        async def list_tools(self) -> Any:
            return await workbench.list_tools()

        def _verdict(self, name: str, arguments: Any) -> Any:
            return preflight.check(
                normalise_tool(str(name)),
                unreadable_arguments(dict(arguments) if arguments else {}),
                # No `session_id`: `call_id` identifies one invocation, so passing it would give
                # every call its own budget tally. See `openai_agents`, which shipped that defect.
            )

        async def call_tool(
            self,
            name: str,
            arguments: Any = None,
            cancellation_token: CancellationToken | None = None,
            call_id: str | None = None,
        ) -> ToolResult:
            verdict = self._verdict(name, arguments)
            if not verdict.proceeds:
                return _refusal(name, verdict.message)
            forwarded: ToolResult = await workbench.call_tool(
                name, arguments, cancellation_token, call_id
            )
            return forwarded

        async def start(self) -> None:
            await workbench.start()

        async def stop(self) -> None:
            await workbench.stop()

        async def reset(self) -> None:
            await workbench.reset()

        async def save_state(self) -> Any:
            return await workbench.save_state()

        async def load_state(self, state: Any) -> None:
            await workbench.load_state(state)

    if streams:

        async def call_tool_stream(
            self: Any,
            name: str,
            arguments: Any = None,
            cancellation_token: CancellationToken | None = None,
            call_id: str | None = None,
        ) -> Any:
            verdict = self._verdict(name, arguments)
            if not verdict.proceeds:
                yield _refusal(name, verdict.message)
                return
            async for event in workbench.call_tool_stream(  # type: ignore[attr-defined]
                name, arguments, cancellation_token, call_id
            ):
                yield event

        _GatedWorkbench.call_tool_stream = call_tool_stream  # type: ignore[attr-defined]

    return _GatedWorkbench()
