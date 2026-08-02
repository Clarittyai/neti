"""A tool-calling loop you wrote yourself — the shape most agents in the world actually are.

Not a framework. An Anthropic Messages loop or an OpenAI Chat Completions loop, where the model
returns a tool call and your code looks the function up by name and calls it:

    for block in message.content:                  # Anthropic
        if block.type == "tool_use":
            result = TOOLS[block.name](**block.input)

    for call in message.tool_calls:                # OpenAI
        result = TOOLS[call.function.name](**json.loads(call.function.arguments))

`Preflight.dispatch` already gates one such call, and `@pf.guard` decorates one such function. Both
are correct and both have the same weakness, which `preflight.py` states rather than hides: an
author who forgets to route one tool through them has no gate on that tool, and nothing detects the
omission. The MCP and hook paths cannot be forgotten that way, which is why they come first.

This closes most of that gap with one substitution:

    from neti.adapters.tool_loop import gate_tools

    TOOLS = gate_tools(pf, TOOLS)                  # once, at the top

Every function in the mapping is wrapped, so the loop below is unchanged and *cannot* be partially
gated. Forgetting is now an all-or-nothing mistake — a tool added to the original dict afterwards is
still ungated, and `neti report` showing no traffic for it is the thing that surfaces that — but the
common failure, gating four of five tools and believing you gated five, is gone.

**A denial comes back as the tool's return value**, a string, exactly as the MCP and SDK seams do
it. The caller's next line hands that back to the model as the tool result, and reading a specific
number is what makes the model retry with a narrower target rather than giving up or repeating
itself. Nothing here raises.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from typing import Any

from neti.preflight import Preflight

__all__ = ["gate_tool", "gate_tools"]


def gate_tool(
    preflight: Preflight, name: str, fn: Callable[..., Any], *, session_id: str | None = None
) -> Callable[..., Any]:
    """Wrap one tool function so the gate runs before it does.

    `name` is the policy key and is passed explicitly rather than read off `fn.__name__`: the name
    the *model* was told is the one a policy is written against, and it is routinely not the name of
    the Python function behind it.

    Only keyword arguments are gated, for the reason `Preflight.guard` gives — a JSON pointer into a
    positional argument would depend on the signature's order, which is exactly the kind of thing
    that silently stops matching after a refactor. Every tool loop worth the name calls with
    keywords anyway, because that is what a JSON object unpacks to.
    """
    from neti.adapters.claude_code import normalise_tool

    key = normalise_tool(name)

    @functools.wraps(fn)
    def gated(*args: Any, **kwargs: Any) -> Any:
        verdict = preflight.check(key, dict(kwargs), session_id=session_id)
        if verdict.proceeds:
            return fn(*args, **kwargs)
        return verdict.message

    return gated


def gate_tools(
    preflight: Preflight,
    tools: Mapping[str, Callable[..., Any]],
    *,
    session_id: str | None = None,
) -> dict[str, Callable[..., Any]]:
    """Every tool in the mapping, gated. Substitute the result for your own dispatch table.

        TOOLS = gate_tools(pf, TOOLS)

    The keys are the names the model sees, which is what a policy is written against — so an
    `mcp__server__tool` name arriving through a federating proxy matches the same policy entry as
    the bare one, the way it does on every other seam.
    """
    return {
        name: gate_tool(preflight, name, fn, session_id=session_id) for name, fn in tools.items()
    }
