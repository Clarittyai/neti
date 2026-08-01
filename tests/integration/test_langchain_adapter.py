"""LangChain and LangGraph, against the real libraries.

The direct-`invoke` tests here all passed against an adapter that was broken in LangGraph, which is
why `test_a_blocked_call_inside_a_real_graph...` exists and runs a compiled `StateGraph` rather
than calling the tool. `ToolNode` hands a whole `ToolCall` to `invoke` and requires a `ToolMessage`
back; the adapter was returning the denial as a bare string, so a blocked call raised
`TypeError: Tool ... returned unexpected type` and killed the graph. A gate that crashes the agent
instead of letting it narrow its scope is a worse outcome than the call it stopped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neti.preflight import Preflight
from tests.integration.test_inventory import EXAMPLE

pytest.importorskip("langchain_core", reason="the SDK extra is not installed")


@pytest.fixture
def pf(tmp_path: Path) -> Preflight:
    return Preflight.demo(EXAMPLE, mode="enforce", records=tmp_path / "d.ndjson")


# ---------------------------------------------------------------------------- the tool wrapper


def test_the_function_does_not_run_when_the_call_is_too_big(pf: Preflight) -> None:
    from langchain_core.tools import tool

    from neti.adapters.langchain_tools import gate_tools

    ran: list[str] = []

    @tool
    def remove_group_members(group: str) -> str:
        """Remove every member of a group."""
        ran.append(group)
        return "removed"

    (gated,) = gate_tools(pf, [remove_group_members])
    out = gated.invoke({"group": "g-eng-all"})

    assert ran == [], "a blocked call must never reach the function"
    assert "41,203" in str(out)


def test_a_call_that_fits_still_runs(pf: Preflight) -> None:
    from langchain_core.tools import tool

    from neti.adapters.langchain_tools import gate_tools

    ran: list[str] = []

    @tool
    def send_email(to: str) -> str:
        """Send a note."""
        ran.append(to)
        return "sent"

    (gated,) = gate_tools(pf, [send_email])
    assert gated.invoke({"to": "g-team"}) == "sent"
    assert ran == ["g-team"]


@pytest.mark.asyncio
async def test_the_async_path_is_gated_too(pf: Preflight) -> None:
    """`ainvoke` is the path LangGraph takes by default in an async graph. Gating only the sync one
    would leave the common case open."""
    from langchain_core.tools import tool

    from neti.adapters.langchain_tools import gate_tools

    ran: list[str] = []

    @tool
    async def remove_group_members(group: str) -> str:
        """Remove every member of a group."""
        ran.append(group)
        return "removed"

    (gated,) = gate_tools(pf, [remove_group_members])
    out = await gated.ainvoke({"group": "g-eng-all"})

    assert ran == []
    assert "41,203" in str(out)


def test_the_schema_the_model_sees_is_identical(pf: Preflight) -> None:
    """An agent must not be able to tell a gated tool from an ungated one by looking at it —
    otherwise the gate leaks into the prompt and into what the model thinks it may attempt.

    Asserted through `convert_to_openai_tool`, which is what `bind_tools` actually sends.
    """
    from langchain_core.tools import tool
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from neti.adapters.langchain_tools import gate_tools

    @tool
    def remove_group_members(group: str) -> str:
        """Remove every member of a group."""
        return "removed"

    (gated,) = gate_tools(pf, [remove_group_members])
    assert convert_to_openai_tool(gated) == convert_to_openai_tool(remove_group_members)


def test_it_is_still_a_basetool(pf: Preflight) -> None:
    """`bind_tools`, `create_react_agent` and `ToolNode` all type-check what they are given, which
    is why this is a delegating subclass rather than a patched method."""
    from langchain_core.tools import BaseTool, tool

    from neti.adapters.langchain_tools import gate_tools

    @tool
    def send_email(to: str) -> str:
        """Send a note."""
        return "sent"

    (gated,) = gate_tools(pf, [send_email])
    assert isinstance(gated, BaseTool)


def test_langchain_callbacks_cannot_gate() -> None:
    """Pinned so nobody 'simplifies' this onto the obvious-looking hook.

    Unlike `RunHooks.on_tool_start` in the OpenAI Agents SDK, this one is annotated `-> Any`, so
    the signature alone looks like it might be able to veto a call. It cannot: the callback manager
    discards what a handler returns. That is only visible by running it, so this runs it — a
    handler that returns an emphatic refusal, and a tool that executes anyway.

    The only way to stop a call from a callback is to raise, which kills the run instead of letting
    the agent read the number and narrow its scope.
    """
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.tools import tool

    ran: list[str] = []

    class Veto(BaseCallbackHandler):
        def on_tool_start(self, serialized: object, input_str: str, **kwargs: object) -> object:
            return {"block": True, "reason": "absolutely not"}

    @tool
    def send_email(to: str) -> str:
        """Send a note."""
        ran.append(to)
        return "sent"

    assert send_email.invoke({"to": "g-team"}, config={"callbacks": [Veto()]}) == "sent"
    assert ran == ["g-team"], "the 'veto' was discarded — callbacks observe, they do not gate"


# ---------------------------------------------------------------------------- LangGraph


def graph(tools: list[object]) -> object:
    """The smallest real graph that executes a tool call — a compiled `StateGraph`, not a direct
    `ToolNode.invoke`, because `ToolNode` needs a LangGraph runtime and only gets one inside one."""
    from langgraph.graph import END, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    g = StateGraph(MessagesState)
    g.add_node("tools", ToolNode(tools))
    g.set_entry_point("tools")
    g.add_edge("tools", END)
    return g.compile()


def call(name: str, args: dict[str, object]) -> object:
    from langchain_core.messages import AIMessage

    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": "1", "type": "tool_call"}]
    )


def test_a_blocked_call_inside_a_real_graph_returns_a_tool_message(pf: Preflight) -> None:
    """The regression test for the defect every other test in this file missed.

    A bare string here raises `TypeError: Tool ... returned unexpected type` inside `ToolNode` and
    takes the whole graph down, which converts a blocked call into a crashed agent.
    """
    pytest.importorskip("langgraph", reason="the SDK extra is not installed")
    from langchain_core.messages import ToolMessage
    from langchain_core.tools import tool

    from neti.adapters.langchain_tools import gate_tools

    ran: list[str] = []

    @tool
    def remove_group_members(group: str) -> str:
        """Remove every member of a group."""
        ran.append(group)
        return "removed"

    app = graph(gate_tools(pf, [remove_group_members]))  # type: ignore[arg-type]
    out = app.invoke({"messages": [call("remove_group_members", {"group": "g-eng-all"})]})  # type: ignore[attr-defined]
    message = out["messages"][-1]

    assert isinstance(message, ToolMessage), "a graph must get back what a graph expects"
    assert message.status == "error", "the call did not happen; the model should not have to infer"
    assert "41,203" in message.content
    assert ran == []


def test_a_permitted_call_inside_a_real_graph_runs_normally(pf: Preflight) -> None:
    pytest.importorskip("langgraph", reason="the SDK extra is not installed")
    from langchain_core.tools import tool

    from neti.adapters.langchain_tools import gate_tools

    ran: list[str] = []

    @tool
    def send_email(to: str) -> str:
        """Send a note."""
        ran.append(to)
        return "sent"

    app = graph(gate_tools(pf, [send_email]))  # type: ignore[arg-type]
    out = app.invoke({"messages": [call("send_email", {"to": "g-team"})]})  # type: ignore[attr-defined]
    message = out["messages"][-1]

    assert message.status == "success"
    assert message.content == "sent"
    assert ran == ["g-team"]
