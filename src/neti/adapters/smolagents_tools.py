"""smolagents tools, gated in front of execution — and one scope limit worth reading.

    from neti.adapters.smolagents_tools import gate_tools

    agent = ToolCallingAgent(tools=gate_tools(pf, [glob]), model=model)

`Tool.__call__` is the seam. It is what both agent types invoke, and it delegates to `forward`,
which is where a tool author puts the work. Gating `__call__` therefore covers `ToolCallingAgent`
and `CodeAgent` alike without smolagents needing to know this exists.

**The limit, stated here rather than discovered later.** `CodeAgent` does not emit tool calls — it
writes Python and executes it, and calling a tool is one of the things that Python may do. Wrapping
the tool object gates every call that goes *through the tool*. It does not gate what the generated
code does directly: `open(...)`, `os.remove(...)`, a `subprocess`, an import of something that
reaches the network. Those never pass a tool boundary, so no gate at a tool boundary can see them.

That is not a defect in this adapter, it is the shape of a code-executing agent, and the thing that
bounds it is the executor smolagents runs the code in — its sandbox, its import allow-list, its
`additional_authorized_imports`. `SCOPE.md` carries this as a non-coverage entry rather than a
footnote, because a reader who assumes otherwise has assumed something dangerous.

**A denial is the return value.** The agent reads the sentence with the number and narrows its
target. Raising would abort the step instead of letting it re-plan.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from neti.preflight import Preflight

__all__ = ["gate_tool", "gate_tools"]


def gate_tool(preflight: Preflight, tool: Any) -> Any:
    """Wrap one smolagents tool so its execution goes through the gate.

    **A real `Tool` subclass, not a delegating object.** The first version of this was a plain class
    that copied the four attributes and forwarded everything else, which passed every direct test
    and then failed the moment an agent was built with it: `ToolCallingAgent` asserts *All elements
    must be instance of BaseTool*. A gate that cannot be handed to the framework's own agent is not
    an integration.

    `forward` therefore takes `**kwargs`, and smolagents validates that a tool's `forward`
    parameters match the keys of its `inputs` — so this sets `skip_forward_signature_validation`,
    which is the flag smolagents sets on its own wrapper tools (`from_langchain`, `from_gradio`)
    for exactly this reason. The inner tool still validates its own arguments when the call
    proceeds, so nothing is skipped that anybody was relying on.

    Name, description, `inputs` and `output_type` are copied verbatim: an agent must not be able to
    tell a gated tool from an ungated one by looking at it, or the gate leaks into the prompt and
    into what the model believes it may attempt.
    """
    from smolagents import Tool

    from neti.adapters.claude_code import normalise_tool
    from neti.core.types import unreadable_arguments

    # smolagents ships no py.typed, so `Tool` is `Any` to mypy and subclassing it is an
    # error it cannot check either way.
    class GatedTool(Tool):  # type: ignore[misc]
        name = tool.name
        description = tool.description
        inputs = tool.inputs
        output_type = tool.output_type
        skip_forward_signature_validation = True

        def forward(self, *args: Any, **kwargs: Any) -> Any:
            verdict = preflight.check(
                normalise_tool(str(tool.name)), unreadable_arguments(dict(kwargs))
            )
            if not verdict.proceeds:
                return verdict.message
            return tool(*args, **kwargs)

    return GatedTool()


def gate_tools(preflight: Preflight, tools: Sequence[Any]) -> list[Any]:
    """Every tool in a list, gated. The form an agent is usually built with."""
    return [gate_tool(preflight, tool) for tool in tools]
