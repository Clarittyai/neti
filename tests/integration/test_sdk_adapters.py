"""The Anthropic and OpenAI adapters, against the real SDKs.

Both are written against APIs that were read rather than remembered — anthropic 0.120,
openai-agents 0.19 — and the reading changed the design once already: `RunHooks.on_tool_start` is
the obvious place to put a gate in the OpenAI Agents SDK, and it returns `None`. An integration
built there would record verdicts and stop nothing, and every test of it would pass.

So these exercise the seams the SDKs actually offer, with real tool objects, and the assertion that
matters in both is the same one: **the function did not run.** A verdict recorded while the tool
executes anyway is the failure that looks most like success.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neti.preflight import Preflight
from tests.integration.test_inventory import EXAMPLE

pytest.importorskip("agents", reason="the SDK extra is not installed")
pytest.importorskip("anthropic", reason="the SDK extra is not installed")


@pytest.fixture
def pf(tmp_path: Path) -> Preflight:
    return Preflight.demo(EXAMPLE, mode="enforce", records=tmp_path / "d.ndjson")


# ---------------------------------------------------------------------------- OpenAI Agents SDK


def guardrail_data(tool_name: str, arguments: str) -> object:
    """The shape the SDK hands a tool input guardrail."""
    from agents import Agent
    from agents.tool_context import ToolContext
    from agents.tool_guardrails import ToolInputGuardrailData

    context = ToolContext(
        context=None,
        tool_name=tool_name,
        tool_call_id="call_1",
        tool_arguments=arguments,
    )
    return ToolInputGuardrailData(context=context, agent=Agent(name="test"))


def test_an_oversized_call_is_rejected_with_the_number(pf: Preflight) -> None:
    """`reject_content` hands the model a sentence instead of running the tool — the same contract
    neti already uses over MCP, arrived at independently by the SDK."""
    from neti.adapters.openai_agents import verdict_for

    out = verdict_for(pf, guardrail_data("remove_group_members", '{"group": "g-eng-all"}'))

    assert out.behavior["type"] == "reject_content"
    assert "41,203" in out.behavior["message"]
    assert out.output_info["neti"]["resolved"] == 41_203


def test_a_call_that_fits_is_allowed(pf: Preflight) -> None:
    from neti.adapters.openai_agents import verdict_for

    out = verdict_for(pf, guardrail_data("send_email", '{"to": "g-team"}'))
    assert out.behavior["type"] == "allow"


def test_an_mcp_prefixed_tool_name_matches_the_same_policy(pf: Preflight) -> None:
    """One policy file governs a tool whichever runtime it arrives through."""
    from neti.adapters.openai_agents import verdict_for

    out = verdict_for(
        pf, guardrail_data("mcp__entra__remove_group_members", '{"group": "g-eng-all"}')
    )
    assert out.behavior["type"] == "reject_content"


def test_unparseable_arguments_do_not_become_an_empty_call(pf: Preflight) -> None:
    """The SDK passes arguments as a JSON string. Treating a malformed one as `{}` would leave the
    policy with no gated parameter to look at, and a parse failure would quietly become a pass."""
    from neti.adapters.openai_agents import verdict_for

    out = verdict_for(pf, guardrail_data("remove_group_members", "{not json"))
    assert out.behavior["type"] == "reject_content", "a call we cannot read must not proceed"


def test_the_guardrail_attaches_to_a_real_tool(pf: Preflight) -> None:
    """It has to be accepted by the SDK's own decorator, not just be callable."""
    from agents import function_tool

    from neti.adapters.openai_agents import neti_guardrail

    @function_tool(tool_input_guardrails=[neti_guardrail(pf)])
    def remove_group_members(group: str) -> str:
        """Remove every member of a group."""
        return "removed"

    assert remove_group_members.tool_input_guardrails
    assert remove_group_members.name == "remove_group_members"


def test_run_hooks_cannot_gate_which_is_why_this_uses_guardrails() -> None:
    """Pinned so nobody 'simplifies' the adapter onto the obvious-looking hook.

    `on_tool_start` returns None. A gate built there would observe every call, block none, and pass
    its own tests.
    """
    import inspect

    from agents import RunHooks

    assert inspect.signature(RunHooks.on_tool_start).return_annotation in ("None", None)


# ---------------------------------------------------------------------------- Anthropic


def test_the_tool_does_not_run_when_the_call_is_too_big(pf: Preflight) -> None:
    """The assertion that matters. A verdict recorded while the function executes anyway is the
    failure that looks most like success."""
    from anthropic.lib.tools import beta_tool

    from neti.adapters.anthropic_tools import gate_tools

    ran: list[str] = []

    @beta_tool
    def remove_group_members(group: str) -> str:
        """Remove every member of a group."""
        ran.append(group)
        return "removed 41,203 people"

    (gated,) = gate_tools(pf, [remove_group_members])
    result = gated.call({"group": "g-eng-all"})

    assert ran == [], "a blocked call must never reach the function"
    assert "41,203" in str(result)


def test_a_call_that_fits_still_runs(pf: Preflight) -> None:
    from anthropic.lib.tools import beta_tool

    from neti.adapters.anthropic_tools import gate_tools

    ran: list[str] = []

    @beta_tool
    def send_email(to: str) -> str:
        """Send a note."""
        ran.append(to)
        return "sent"

    (gated,) = gate_tools(pf, [send_email])
    assert gated.call({"to": "g-team"}) == "sent"
    assert ran == ["g-team"]


def test_the_schema_the_model_sees_is_untouched(pf: Preflight) -> None:
    """An agent must not be able to tell a gated tool from an ungated one by looking at it —
    otherwise the gate leaks into the prompt and into what the model thinks it may attempt."""
    from anthropic.lib.tools import beta_tool

    from neti.adapters.anthropic_tools import gate_tools

    @beta_tool
    def send_email(to: str) -> str:
        """Send a note to a group."""
        return "sent"

    before = send_email.to_dict()
    (gated,) = gate_tools(pf, [send_email])
    assert gated.to_dict() == before


def test_the_denial_is_returned_not_raised(pf: Preflight) -> None:
    """`tool_runner` feeds a return value back to the model as the tool result. Raising would abort
    the run, and a run that has died cannot narrow its scope and try again."""
    from anthropic.lib.tools import beta_tool

    from neti.adapters.anthropic_tools import gate_tools

    @beta_tool
    def remove_group_members(group: str) -> str:
        """Remove every member of a group."""
        return "removed"

    (gated,) = gate_tools(pf, [remove_group_members])
    out = gated.call({"group": "g-eng-all"})  # must not raise
    assert isinstance(out, str)
    assert "Narrow the target" in out
