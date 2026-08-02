"""Google ADK, gated through the callback the framework already has for this.

ADK is the closest fit of any runtime here, because its own contract is written in exactly these
terms. `BasePlugin.before_tool_callback` runs before every tool call and its return value decides:

    None                       -> proceed, and ADK calls the tool
    a non-empty dict           -> the tool does NOT run, and the dict becomes the FunctionResponse

So a denial is a first-class outcome the model reads and re-plans around, not an exception somebody
has to catch. Nothing is wrapped, which means the schema the model is shown is byte-for-byte what
the author registered — a gated tool is indistinguishable from an ungated one, which is the
requirement every adapter here is held to.

**The plugin, not the agent callback.** `LlmAgent(before_tool_callback=...)` exists and works, and
this uses the plugin instead for two reasons. It runs *before* any agent-level callback, and it is
attached once to the app rather than once per agent — so an operator cannot gate the root agent,
forget a sub-agent, and be left with a hole the shape of one delegation. Coverage that depends on
remembering is the failure mode `preflight.py` already warns about for the in-process seam.

**Non-empty matters.** The agent-level path tests the returned dict for truthiness, so `{}` there
would fall through and run the tool. The response always carries the sentence, so this cannot
arise — but it is why the denial is built as a dict with content in it rather than as a flag.
"""

from __future__ import annotations

from typing import Any

from neti.preflight import Preflight

__all__ = ["neti_plugin"]


def _decide(preflight: Preflight, tool: Any, tool_args: Any, tool_context: Any) -> Any:
    """One tool call, in ADK's vocabulary. `None` proceeds; a dict replaces the call."""
    from neti.adapters.claude_code import normalise_tool
    from neti.core.types import unreadable_arguments

    del tool_context  # `function_call_id` identifies one call, not a session — see openai_agents
    verdict = preflight.check(
        normalise_tool(str(getattr(tool, "name", "") or "")),
        unreadable_arguments(tool_args),
    )
    if verdict.proceeds:
        return None
    # `error` rather than a bare string: ADK hands the whole dict to the model as the tool's
    # response, and naming the field is what tells it this is a refusal rather than a result.
    return {"error": verdict.message, "neti": verdict.payload}


def neti_plugin(preflight: Preflight) -> Any:
    """A plugin bound to a `Preflight`, for `App(plugins=[...])`.

        pf = Preflight.from_config("neti.yaml")
        app = App(name="ops", root_agent=agent, plugins=[neti_plugin(pf)])

    Every tool call in the app goes through it, including sub-agents'.
    """
    from google.adk.plugins.base_plugin import BasePlugin

    class _NetiPlugin(BasePlugin):
        def __init__(self) -> None:
            super().__init__(name="neti")

        async def before_tool_callback(
            self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any
        ) -> dict[str, Any] | None:
            decided: dict[str, Any] | None = _decide(preflight, tool, tool_args, tool_context)
            return decided

    return _NetiPlugin()
