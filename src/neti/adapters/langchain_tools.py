"""LangChain and LangGraph tools, gated in front of execution.

    from neti.adapters.langchain_tools import gate_tools

    tools = gate_tools(pf, [remove_group_members, send_email])
    agent = create_react_agent(model, tools)          # or llm.bind_tools(tools)

Works for LangGraph too, because LangGraph's `ToolNode` executes a tool by calling `invoke`, and
`BaseTool.invoke` delegates to `run`. Gating `run` therefore covers both without LangGraph being a
dependency or even installed.

**Why a wrapper class and not a patched method.** The Anthropic adapter replaces `tool.call` on the
instance, which is the least invasive thing that can work there. It cannot work here: LangChain's
`BaseTool` is a pydantic model, so `tool.run = ...` raises `ValueError: "StructuredTool" object has
no field "run"`. The wrapper is a real `BaseTool` that delegates, which is also what makes it
acceptable to `bind_tools`, `create_react_agent` and `ToolNode` — all of which type-check what they
are given.

**Not a callback handler.** `BaseCallbackHandler.on_tool_start` is the obvious-looking place and is
a worse trap than the equivalent in the OpenAI Agents SDK, because that one is annotated `-> None`
and announces itself. LangChain's is annotated `-> Any`, so it reads like something that could veto
a call — and the callback manager throws the return value away. A gate built there would observe
every call, stop none, and look correct in review. The only way to halt a call from a callback is
to raise, which kills the run instead of letting the agent narrow its scope. Demonstrated by
running it, in `test_langchain_callbacks_cannot_gate`.

**A denial is the tool's return value.** The agent reads the sentence with the number in it and
re-plans, which is the same contract as every other adapter here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from neti.preflight import Preflight

if TYPE_CHECKING:  # pragma: no cover - import only for types
    from langchain_core.tools import BaseTool

__all__ = ["gate_tool", "gate_tools"]


def _gated_tool_class() -> Any:
    """Built on first use so importing this module never requires langchain-core."""
    from langchain_core.tools import BaseTool

    class GatedTool(BaseTool):
        """A tool that sizes the call before letting the inner tool see it.

        Everything the model is shown — name, description, `args_schema` — is copied from the inner
        tool unchanged. An agent must not be able to tell a gated tool from an ungated one by
        looking at it, or the gate leaks into the prompt and into what the model believes it may
        attempt.
        """

        # Annotated opaquely on purpose. `BaseTool` is a pydantic model, so every annotation here
        # becomes a validation schema, and pydantic cannot build one for `Preflight` — it walks
        # into the engine's dataclasses and fails on a resolver protocol. These two are internal
        # plumbing that no model ever sees, so there is nothing to validate.
        inner: Any
        preflight: Any

        def _run(self, *args: Any, **kwargs: Any) -> Any:
            # Unreachable in practice: `run` below is overridden and never falls through to here.
            # BaseTool declares `_run` abstract, so it has to exist.
            return self.inner._run(*args, **kwargs)

        # `*args` as well as `**kwargs`: `BaseTool.run` takes `verbose`, `start_color` and `color`
        # positionally, and an override that accepted only keywords would break any caller using
        # them — while type-checking clean against a `**kwargs`-only signature. Everything is
        # forwarded untouched; `tool_call_id` is keyword-only upstream, so reading it below is safe.
        def run(self, tool_input: str | dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            verdict = self._verdict(tool_input)
            if verdict is not None:
                return self._denial(verdict, kwargs.get("tool_call_id"))
            return self.inner.run(tool_input, *args, **kwargs)

        async def arun(self, tool_input: str | dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            verdict = self._verdict(tool_input)
            if verdict is not None:
                return self._denial(verdict, kwargs.get("tool_call_id"))
            return await self.inner.arun(tool_input, *args, **kwargs)

        def _denial(self, message: str, tool_call_id: str | None) -> Any:
            """Shaped like whatever the inner tool would have returned.

            Returning a bare string here is wrong in exactly one place, and it is the place that
            matters: LangGraph's `ToolNode` calls `invoke` with a whole `ToolCall`, and `BaseTool`
            answers those with a `ToolMessage`. A string comes back as
            `TypeError: Tool ... returned unexpected type` and takes the graph down — turning a
            blocked call into a crashed agent instead of one that reads the number and narrows its
            scope. Found by running a real compiled graph; a direct `invoke({...})` never sees it.

            `status="error"` for the same reason MCP denials carry `isError: true`: the call did not
            happen, and the model is entitled to know that rather than infer it from prose.
            """
            if tool_call_id is None:
                return message
            from langchain_core.messages import ToolMessage

            return ToolMessage(
                content=message, tool_call_id=tool_call_id, name=self.name, status="error"
            )

        def _verdict(self, tool_input: str | dict[str, Any]) -> str | None:
            """The denial sentence, or `None` to proceed."""
            from neti.adapters.claude_code import normalise_tool

            if isinstance(tool_input, dict):
                args: dict[str, Any] = tool_input
            else:
                # A single-argument tool can be invoked with a bare string. There is no parameter
                # name to bind a gate to, so it is passed under a name a policy can declare rather
                # than dropped — dropping it would make the call look empty and pass.
                args = {"__input__": tool_input}
            outcome = self.preflight.check(normalise_tool(self.name), args)
            return None if outcome.proceeds else outcome.message

    return GatedTool


def gate_tool(preflight: Preflight, tool: BaseTool) -> BaseTool:
    """One tool, gated. The returned object is a `BaseTool` and goes anywhere the original did."""
    cls = _gated_tool_class()
    return cls(  # type: ignore[no-any-return]
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        return_direct=tool.return_direct,
        metadata=tool.metadata,
        tags=tool.tags,
        inner=tool,
        preflight=preflight,
    )


def gate_tools(preflight: Preflight, tools: Sequence[BaseTool]) -> list[BaseTool]:
    """Every tool in the list, gated. Hand the result to `bind_tools` or `create_react_agent`."""
    return [gate_tool(preflight, tool) for tool in tools]
