"""CrewAI, gated by wrapping the tool — because its hooks cannot carry the sentence.

**`gate_tools` is the one to use.** `install` is kept, and documented below, for the case where
somebody wants the decision recorded without touching their tool objects.

`before_tool_call` looks like the seam, and it can stop a call: returning `False` blocks it. What it
cannot do is say *why*. CrewAI substitutes a fixed string when a hook blocks:

    result = f"Tool execution blocked by hook. Tool: {func_name}"

That sentence is the whole product. Losing it leaves the model with "blocked" and nothing to act on
— no magnitude, no ceiling, and so no reason to retry with a narrower target instead of giving up
or repeating itself. `HookAborted(reason=...)` does not help either: the dispatcher catches it and
discards the reason.

**This module used to claim `after_tool_call` put the sentence back, and that was wrong.**
`crewai/utilities/tool_utils.py` returns the moment a before-hook answers `False`:

    for hook in before_hooks:
        if hook(hook_context) is False:
            return ToolResult(blocked_message, False)   # after-hooks never reached

There is exactly one call site for the hook pair, so there is no executor path where the after-hook
runs for a blocked call. The pairing could not work and never had. It went unnoticed because the
seam test drove a *reproduction* of CrewAI's control flow that ran the after-hooks anyway, and the
reproduction was wrong in precisely the way that made the test pass. `tests/conformance/` found it
by running `Crew.kickoff()` instead, and the agent's observation read:

    Observation: Tool execution blocked by hook. Tool: Glob

So the gate moved to where every other adapter in this package already puts it: the tool. A wrapped
`BaseTool` sizes the call in `_run` and returns the sentence, which CrewAI hands to the model as an
ordinary tool result — the same contract as LangChain, the Anthropic runner and the rest. A denial
is a return value.

**A `ContextVar`, not a dict keyed on the call.** In `crew_agent_executor` the before and after
contexts are built separately from `args_dict or {}`, which produces two *distinct* empty dicts when
a tool takes no arguments — so keying on argument identity would silently mismatch on exactly the
calls that are easiest to get wrong. Both hooks run in the same coroutine, so a ContextVar is
correct, and it also keeps concurrent tool calls under `asyncio.gather` from reading each other's
sentence.

**The hooks must be sync.** `run_hooks` calls the function and inspects the return value; an
`async def` hook returns a coroutine, which is truthy and is not `False`, so it would allow every
call while looking installed. That is the worst failure available to a gate, so it is asserted in a
test rather than trusted.
"""

from __future__ import annotations

import contextvars
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from neti.preflight import Preflight

if TYPE_CHECKING:  # pragma: no cover - import only for types
    from crewai.hooks import ToolCallHookContext

__all__ = ["clear", "gate_tool", "gate_tools", "install"]


def _gated_tool_class() -> Any:
    """Built on first use so importing this module never requires crewai."""
    from crewai.tools import BaseTool

    class GatedTool(BaseTool):
        """A CrewAI tool that sizes the call before the inner tool sees it.

        Name, description and `args_schema` are copied from the inner tool unchanged: an agent must
        not be able to tell a gated tool from an ungated one by looking at it, or the gate leaks
        into the prompt and into what the model believes it may attempt.
        """

        # Opaque on purpose, exactly as in `langchain_tools`. `BaseTool` is a pydantic model, so
        # every annotation becomes a validation schema, and pydantic cannot build one for
        # `Preflight` — it walks into the engine's dataclasses and fails on a resolver protocol.
        inner: Any = None
        preflight: Any = None

        def _run(self, *args: Any, **kwargs: Any) -> Any:
            from neti.adapters.claude_code import normalise_tool
            from neti.core.types import unreadable_arguments

            verdict = self.preflight.check(
                normalise_tool(str(self.name)), unreadable_arguments(dict(kwargs))
            )
            if not verdict.proceeds:
                return verdict.message
            return self.inner.run(*args, **kwargs)

    return GatedTool


def gate_tool(preflight: Preflight, tool: Any) -> Any:
    """Wrap one CrewAI tool so its execution goes through the gate.

        pf = Preflight.from_config("neti.yaml")
        agent = Agent(role="ops", goal=..., backstory=..., tools=gate_tools(pf, [glob]))

    A denial is the tool's return value, so the model reads the sentence with the number in it and
    re-plans. Nothing is raised: a crew whose run has died cannot narrow its scope.
    """
    return _gated_tool_class()(
        name=tool.name,
        description=tool.description,
        args_schema=getattr(tool, "args_schema", None),
        inner=tool,
        preflight=preflight,
    )


def gate_tools(preflight: Preflight, tools: Sequence[Any]) -> list[Any]:
    """Every tool in a list, gated. The form an agent is usually built with."""
    return [gate_tool(preflight, tool) for tool in tools]


_DENIAL: contextvars.ContextVar[str | None] = contextvars.ContextVar("neti_denial", default=None)


def install(preflight: Preflight, *, tools: list[str] | None = None) -> None:
    """Register the gate on CrewAI's global hook queue.

        pf = Preflight.from_config("neti.yaml")
        install(pf)

    `tools=` narrows it to named tools; left out, every tool call the crew makes goes through it.
    Registration is global because CrewAI's hooks are — there is no per-crew queue to attach to.

    **This stops the call and records it, and the model is told only "Tool execution blocked by
    hook".** CrewAI returns its own fixed string the moment a before-hook answers `False`, and no
    after-hook runs on that path, so the magnitude never reaches the agent. Use `gate_tools` if you
    want the agent to be able to narrow its target — which is the entire point of the number.
    """
    from crewai.hooks import after_tool_call, before_tool_call

    from neti.adapters.claude_code import normalise_tool
    from neti.core.types import unreadable_arguments

    @before_tool_call(tools=tools)
    def _gate(context: ToolCallHookContext) -> bool | None:
        verdict = preflight.check(
            normalise_tool(str(getattr(context, "tool_name", "") or "")),
            unreadable_arguments(getattr(context, "tool_input", None)),
        )
        if verdict.proceeds:
            _DENIAL.set(None)
            return None
        _DENIAL.set(verdict.message)
        return False

    @after_tool_call(tools=tools)
    def _explain(context: ToolCallHookContext) -> str | None:
        del context
        message = _DENIAL.get()
        _DENIAL.set(None)
        return message


def clear() -> None:
    """Unregister every tool-call hook. For tests, and for a process that reconfigures itself."""
    from crewai.hooks import clear_all_tool_call_hooks

    clear_all_tool_call_hooks()
