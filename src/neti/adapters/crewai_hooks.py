"""CrewAI, gated through its tool-call hooks — which takes two of them, for one specific reason.

`before_tool_call` is the seam, and it can stop a call: returning `False` blocks it. What it cannot
do is say why. Every one of CrewAI's executor paths substitutes a fixed string when a hook blocks:

    result = f"Tool execution blocked by hook. Tool: {func_name}"

That sentence is the whole product, and losing it would leave the model with "blocked" and nothing
to act on — no magnitude, no ceiling, and therefore no reason to retry with a narrower target
instead of giving up or repeating itself. `HookAborted(reason=...)` does not help either: the
dispatcher catches it and discards the reason.

So the gate is a *pair*. `before_tool_call` decides and stashes the sentence; `after_tool_call` runs
even for a blocked call and a `str` return from it replaces the result. The verdict comes from the
first, the words from the second.

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
from typing import TYPE_CHECKING

from neti.preflight import Preflight

if TYPE_CHECKING:  # pragma: no cover - import only for types
    from crewai.hooks import ToolCallHookContext

__all__ = ["clear", "install"]

_DENIAL: contextvars.ContextVar[str | None] = contextvars.ContextVar("neti_denial", default=None)


def install(preflight: Preflight, *, tools: list[str] | None = None) -> None:
    """Register the gate on CrewAI's global hook queue.

        pf = Preflight.from_config("neti.yaml")
        install(pf)

    `tools=` narrows it to named tools; left out, every tool call the crew makes goes through it.
    Registration is global because CrewAI's hooks are — there is no per-crew queue to attach to.
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
