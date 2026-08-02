"""Whatever else goes wrong, the gate must not take the agent down with it.

This is the asymmetry that makes a preflight gate different from an ordinary library. A resolver
that fails, a policy that will not parse, a records file on a full disk — each of those should stop
*one* call, loudly, in the direction the operator declared. None of them may crash the process,
because of where the process sits:

- **`neti hook` runs on every tool call in a Claude Code session.** A non-zero exit or a traceback
  on stdout is not one failed call, it is every subsequent tool call in that session failing, and
  the user's only recovery is to work out that a hook they installed last week is the cause.
- **An adapter that raises kills the run.** The whole design depends on a denial coming back as a
  tool *result* the model can read and re-plan around. An exception ends the loop instead, so a
  call that was merely too big becomes an agent that stopped working.

`test_a_broken_policy_never_takes_down_a_claude_code_session` already covers one instance of this.
This generalises it: Hypothesis-generated garbage on stdin, and induced failures in each part the
gate depends on, against every seam that owns a call's execution.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from neti.preflight import Preflight
from tests.integration.test_inventory import EXAMPLE

HOOK = [sys.executable, "-m", "neti.cli", "hook"]


def run_hook_process(stdin: str, config: Path, records: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*HOOK, "--config", str(config), "--records", str(records), "--demo"],
        capture_output=True,
        text=True,
        input=stdin,
        timeout=60,
    )


@pytest.fixture
def config(tmp_path: Path) -> Path:
    target = tmp_path / "neti.yaml"
    target.write_text(EXAMPLE.read_text())
    return target


def assert_survivable(out: subprocess.CompletedProcess[str], what: str) -> None:
    """Exit 0, and stdout is either empty or JSON Claude Code can act on.

    Nothing weaker is enough. A hook that exits non-zero fails the tool call it was asked about;
    a hook that prints a traceback to stdout corrupts the decision protocol itself.
    """
    assert out.returncode == 0, f"{what}: exited {out.returncode}\nstderr: {out.stderr[-1500:]}"
    if out.stdout.strip():
        try:
            parsed = json.loads(out.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover - the failure path is the point
            pytest.fail(f"{what}: stdout is not JSON ({exc})\n{out.stdout[:800]}")
        assert isinstance(parsed, dict), f"{what}: stdout is not an object"
    assert "Traceback" not in out.stdout, f"{what}: a traceback reached stdout"


# ---------------------------------------------------------------------------- fuzzed input


# Deliberately hostile: an event shape the hook was not built for is an operator wiring it to a
# broader matcher than intended, which is a configuration slip and not a reason to deny everything.
events = st.one_of(
    st.text(max_size=200),
    st.just(""),
    st.just("null"),
    st.just("[]"),
    st.just("{}"),
    st.just('{"tool_name": null}'),
    st.just('{"hook_event_name": "PreToolUse"}'),
    st.just('{"hook_event_name": "PreToolUse", "tool_name": 42, "tool_input": []}'),
    st.just('{"hook_event_name": "Stop", "tool_name": "send_email"}'),
    st.builds(
        lambda tool, args: json.dumps(
            {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": args}
        ),
        tool=st.text(max_size=60),
        args=st.dictionaries(st.text(max_size=20), st.text(max_size=60), max_size=4),
    ),
)


# 15 rather than a few hundred: every example is a subprocess, and interpreter startup dominates.
# The generated cases are a net for shapes nobody thought of; the shapes anybody *has* thought of
# are enumerated in `test_specific_hostile_inputs`, which is where a regression should be pinned.
@given(event=events)
@settings(
    max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_no_input_can_crash_the_hook(event: str, config: Path, tmp_path: Path) -> None:
    """Anything at all on stdin, and the session keeps working."""
    assert_survivable(run_hook_process(event, config, tmp_path / "d.ndjson"), repr(event[:60]))


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("empty stdin", ""),
        ("not json", "}{"),
        ("json but not an object", "[1, 2, 3]"),
        (
            "unicode and control characters in the tool name",
            json.dumps({"tool_name": "\u202e\u0000\U0001f4a5", "tool_input": {}}),
        ),
        (
            "huge argument",
            json.dumps({"tool_name": "send_email", "tool_input": {"to": "x" * 80_000}}),
        ),
        (
            "nested where a string was expected",
            json.dumps({"tool_name": "send_email", "tool_input": {"to": {"a": {"b": {}}}}}),
        ),
        ("null tool_input", json.dumps({"tool_name": "send_email", "tool_input": None})),
    ],
)
def test_specific_hostile_inputs(name: str, payload: str, config: Path, tmp_path: Path) -> None:
    """The cases worth naming, kept alongside the fuzz so a failure reads as a sentence."""
    assert_survivable(run_hook_process(payload, config, tmp_path / "d.ndjson"), name)


# ---------------------------------------------------------------------------- induced failures


def test_an_argument_that_breaks_the_provider_is_decided_not_allowed(tmp_path: Path) -> None:
    """Surviving is necessary and not sufficient — the call still has to be decided correctly.

    The 80,000-character argument above reaches httpx as a URL and raises `InvalidURL: URL too
    long`. Containing that inside the engine is what stops the session dying; routing it through the
    declared `on_unresolved` is what stops it becoming a silent pass. The shipped example is
    `mode: observe`, which forwards everything, so this asserts against enforce — the mode where
    getting it wrong would matter.
    """
    from neti.preflight import Preflight

    pf = Preflight.demo(EXAMPLE, mode="enforce", records=tmp_path / "d.ndjson")
    verdict = pf.check("send_email", {"to": "x" * 80_000})

    assert not verdict.proceeds, "a call the resolver could not survive must not be allowed"
    assert verdict.rule.endswith("on_unresolved:unresolved")
    assert verdict.payload["resolved"] is None


def test_a_policy_that_will_not_parse_still_lets_the_session_run(tmp_path: Path) -> None:
    """The instance that already had a test, kept because it is the likeliest one in practice."""
    broken = tmp_path / "broken.yaml"
    broken.write_text("version: 1\ntools:\n  send_email:\n    gate:\n      /to: {resolver: nope}\n")
    event = json.dumps({"tool_name": "send_email", "tool_input": {"to": "g-team"}})
    assert_survivable(run_hook_process(event, broken, tmp_path / "d.ndjson"), "unknown resolver")


def test_a_policy_file_that_is_not_there_still_lets_the_session_run(tmp_path: Path) -> None:
    event = json.dumps({"tool_name": "send_email", "tool_input": {"to": "g-team"}})
    assert_survivable(
        run_hook_process(event, tmp_path / "absent.yaml", tmp_path / "d.ndjson"), "missing policy"
    )


def test_an_unwritable_records_path_still_lets_the_session_run(
    config: Path, tmp_path: Path
) -> None:
    """Recording is evidence, not the decision. Losing the ability to write must not become an
    inability to answer — a full disk would otherwise take out every agent on the machine."""
    blocked = tmp_path / "records-dir"
    blocked.mkdir()  # a directory where a file is expected
    event = json.dumps({"tool_name": "send_email", "tool_input": {"to": "g-team"}})
    assert_survivable(run_hook_process(event, config, blocked), "unwritable records path")


def test_a_resolver_that_raises_stops_one_call_and_not_the_process(tmp_path: Path) -> None:
    """A provider can fail in ways nobody anticipated. The declared `on_unresolved` owns that."""
    from neti.config.policy import Policy
    from neti.core.types import ProposedCall, Resolution
    from neti.core.units import Unit
    from neti.engine import Engine
    from neti.gatekeeper import Gatekeeper

    class Exploding:
        unit = Unit.PRINCIPALS
        breakdown_keys: frozenset[str] = frozenset()

        def resolve(self, target: str, ctx: Any) -> Resolution:
            raise RuntimeError("the provider fell over")

        def reachable_max(self, ctx: Any) -> Resolution:
            raise RuntimeError("the provider fell over")

    policy = Policy.model_validate(
        {
            "version": 1,
            "mode": "enforce",
            "tools": {"act": {"gate": {"/target": {"resolver": "boom", "on_unresolved": "block"}}}},
        }
    )
    engine = Engine(policy=policy, resolvers={"boom": Exploding()})
    decision = Gatekeeper(engine=engine).decide(ProposedCall(tool="act", args={"target": "x"}))

    assert decision.record.verdict == "block", "a failed provider takes the declared verdict"
    assert decision.record.causes[0]["magnitude"] is None


# ---------------------------------------------------------------------------- the SDK adapters


def preflight(tmp_path: Path) -> Preflight:
    return Preflight.demo(EXAMPLE, mode="enforce", records=tmp_path / "d.ndjson")


def test_the_anthropic_adapter_returns_a_denial_rather_than_raising(tmp_path: Path) -> None:
    """`tool_runner` feeds a return value back to the model. Raising ends the run, and a run that
    has died cannot narrow its scope and try again."""
    pytest.importorskip("anthropic", reason="the sdks extra is not installed")
    from anthropic.lib.tools import beta_tool

    from neti.adapters.anthropic_tools import gate_tools

    @beta_tool
    def remove_group_members(group: str) -> str:
        """Remove every member of a group."""
        return "removed"

    (gated,) = gate_tools(preflight(tmp_path), [remove_group_members])
    assert isinstance(gated.call({"group": "g-eng-all"}), str)


def test_the_langchain_adapter_returns_a_denial_rather_than_raising(tmp_path: Path) -> None:
    pytest.importorskip("langchain_core", reason="the sdks extra is not installed")
    from langchain_core.tools import tool

    from neti.adapters.langchain_tools import gate_tools

    @tool
    def remove_group_members(group: str) -> str:
        """Remove every member of a group."""
        return "removed"

    (gated,) = gate_tools(preflight(tmp_path), [remove_group_members])
    assert "41,203" in str(gated.invoke({"group": "g-eng-all"}))


def test_the_openai_adapter_rejects_rather_than_raising(tmp_path: Path) -> None:
    """`reject_content` rather than `raise_exception`: the model reads the number and re-plans."""
    pytest.importorskip("agents", reason="the sdks extra is not installed")
    from agents import Agent
    from agents.tool_context import ToolContext
    from agents.tool_guardrails import ToolInputGuardrailData

    from neti.adapters.openai_agents import verdict_for

    data = ToolInputGuardrailData(
        context=ToolContext(
            context=None,
            tool_name="remove_group_members",
            tool_call_id="c1",
            tool_arguments='{"group": "g-eng-all"}',
        ),
        agent=Agent(name="test"),
    )
    assert verdict_for(preflight(tmp_path), data).behavior["type"] == "reject_content"


@pytest.mark.parametrize("args", ["", "not json", "[]", "null", '{"group": null}'])
def test_unreadable_arguments_never_become_a_pass(args: str, tmp_path: Path) -> None:
    """The failure mode that looks most like success.

    Treating arguments we could not parse as `{}` leaves the policy with no gated parameter to look
    at, and the call sails through — a parse error quietly becoming permission.
    """
    pytest.importorskip("agents", reason="the sdks extra is not installed")
    from agents import Agent
    from agents.tool_context import ToolContext
    from agents.tool_guardrails import ToolInputGuardrailData

    from neti.adapters.openai_agents import verdict_for

    data = ToolInputGuardrailData(
        context=ToolContext(
            context=None,
            tool_name="remove_group_members",
            tool_call_id="c1",
            tool_arguments=args,
        ),
        agent=Agent(name="test"),
    )
    assert verdict_for(preflight(tmp_path), data).behavior["type"] == "reject_content", (
        f"arguments {args!r} were treated as an empty, allowable call"
    )


# ---------------------------------------------------------------------------- the other four
#
# Same contract, four more runtimes. Each of these signals a refusal in its own vocabulary, and the
# only thing that matters here is that none of them signals it by ending the run: an exception
# escaping the gate turns "this call was too big" into "the agent stopped working", which is a
# strictly worse outcome than not having installed a gate at all.


def test_the_google_adk_plugin_returns_a_response_rather_than_raising(tmp_path: Path) -> None:
    """ADK skips the tool when the callback returns a dict. A raise would abort the invocation."""
    pytest.importorskip("google.adk", reason="the sdks-extended extra is not installed")
    import asyncio
    from types import SimpleNamespace

    from neti.adapters.google_adk import neti_plugin

    plugin = neti_plugin(preflight(tmp_path))
    out = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name="remove_group_members"),
            tool_args={"group": "g-eng-all"},
            tool_context=None,
        )
    )
    assert isinstance(out, dict) and out, "an empty dict falls through and runs the tool"
    assert "41,203" in out["error"]


def test_the_pydantic_ai_hook_raises_only_the_exception_the_run_survives(tmp_path: Path) -> None:
    """The one adapter that signals by raising, which is safe *only* because of which exception.

    `ToolFailed` is caught by the framework and becomes a failed tool result; anything else ends the
    run. So this asserts the type, not merely that something was raised — the distinction is the
    whole reason this adapter is allowed to raise at all.
    """
    pytest.importorskip("pydantic_ai", reason="the sdks-extended extra is not installed")
    import asyncio

    from pydantic_ai.exceptions import ToolFailed
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

    from neti.adapters.pydantic_ai import neti_hooks

    hooks = neti_hooks(preflight(tmp_path))
    args = {"group": "g-eng-all"}

    async def run() -> None:
        await hooks.before_tool_execute(
            None,
            call=ToolCallPart(tool_name="remove_group_members", args=args, tool_call_id="c1"),
            tool_def=ToolDefinition(name="remove_group_members"),
            args=args,
        )

    with pytest.raises(ToolFailed) as raised:
        asyncio.run(run())
    assert "41,203" in str(raised.value.message)


def test_the_autogen_workbench_returns_an_error_result_rather_than_raising(tmp_path: Path) -> None:
    """`is_error=True` is what makes the model read this as a refusal.

    Returning the sentence as an ordinary result would have it read as a tool that succeeded and
    happened to return that text, which is the failure this asserts against rather than assumes.
    """
    pytest.importorskip("autogen_core", reason="the sdks-extended extra is not installed")
    import asyncio

    from autogen_core.tools import StaticWorkbench

    from neti.adapters.autogen_tools import gate_workbench

    bench = gate_workbench(preflight(tmp_path), StaticWorkbench(tools=[]))
    result = asyncio.run(bench.call_tool("remove_group_members", {"group": "g-eng-all"}))
    assert result.is_error
    assert "41,203" in str(result.result[0].content)


def test_the_crewai_hooks_must_be_synchronous(tmp_path: Path) -> None:
    """The failure mode that would look installed and gate nothing.

    CrewAI's dispatcher calls the hook and inspects its return value. An `async def` hook returns a
    coroutine — which is truthy and is not `False` — so it would allow every call while appearing
    correctly registered. That is the worst outcome available to a gate, so the shape is asserted
    rather than trusted.
    """
    pytest.importorskip("crewai", reason="the sdks-extended extra is not installed")
    import inspect

    from crewai.hooks import clear_all_tool_call_hooks, get_before_tool_call_hooks

    from neti.adapters.crewai_hooks import install

    clear_all_tool_call_hooks()
    try:
        install(preflight(tmp_path))
        hooks = get_before_tool_call_hooks()
        assert hooks, "the gate registered nothing"
        for hook in hooks:
            assert not inspect.iscoroutinefunction(hook), (
                "an async before-hook returns a coroutine, which CrewAI reads as 'not blocked'"
            )
    finally:
        clear_all_tool_call_hooks()
