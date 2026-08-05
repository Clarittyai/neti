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

    The instance is wrapped rather than subclassed: smolagents builds `Tool` subclasses with class
    attributes (`name`, `description`, `inputs`, `output_type`) and validates them at construction,
    so a generic subclass would have to invent values for all four and would then be describing
    itself rather than the tool it stands in front of. Delegating leaves the description the model
    is shown byte-identical.
    """
    from neti.adapters.claude_code import normalise_tool
    from neti.core.types import unreadable_arguments

    class GatedTool:
        # Everything the model is shown comes from the inner tool, unchanged.
        name = tool.name
        description = tool.description
        inputs = tool.inputs
        output_type = tool.output_type

        def __getattr__(self, item: str) -> Any:
            """Anything smolagents asks for that is not the call itself."""
            return getattr(tool, item)

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            verdict = preflight.check(
                normalise_tool(str(tool.name)), unreadable_arguments(dict(kwargs))
            )
            if not verdict.proceeds:
                return verdict.message
            return tool(*args, **kwargs)

        def forward(self, *args: Any, **kwargs: Any) -> Any:
            """`__call__` is what agents use, but a caller reaching past it must not escape.

            smolagents' own `__call__` delegates here, so a wrapper that gated only `__call__` would
            be bypassed by anything that called `forward` directly — including a tool that another
            tool calls internally.
            """
            return self(*args, **kwargs)

    return GatedTool()


def gate_tools(preflight: Preflight, tools: Sequence[Any]) -> list[Any]:
    """Every tool in a list, gated. The form an agent is usually built with."""
    return [gate_tool(preflight, tool) for tool in tools]
