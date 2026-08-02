"""One policy, one call, eleven seams — and they must agree exactly.

`neti` sits in front of an agent through whichever door that agent already has: MCP over stdio or
HTTP, Claude Code's `PreToolUse` hook, the Anthropic tool runner, the OpenAI Agents SDK, LangChain
and LangGraph, CrewAI, Pydantic AI, AutoGen, Google ADK, or a loop somebody wrote themselves. Each
has its own adapter, its own argument shape, and its own idea of what "refuse" looks like — a
JSON-RPC result, a hook decision, a `ToolMessage`, a guardrail rejection, a `ToolResult` with
`is_error`, a raised `ToolFailed`, a dict that replaces the call, a returned string.

**A verdict that depends on which door the call came through is a bug in the product, not in the
adapter.** The codebase already believed this — `test_hook_denial_reads_the_same_as_the_mcp_denial`
and `test_the_denial_is_word_for_word_the_hook_denial` compare two seams each — but those were
written before the SDK adapters existed, so most of the seams sat outside any such check. This is
those tests generalised into one table: every seam is a row, every scenario is a column, and adding
a seam means adding a row rather than remembering to write a comparison — enforced, because
`test_the_seam_table_covers_every_shipped_adapter` fails the build on an adapter with no row.

The other axis is `tests/e2e/worlds.py`. Every case names a world, so each seam is driven across
every resolver family rather than against Entra alone.

What is asserted is deliberately strict: the same **verdict**, the same **magnitude**, and the same
**sentence**, byte for byte. Wording is included because the sentence is the product's actual
output — it is what the model reads and what makes it retry with a narrower target — and a seam that
quietly rephrases it has changed the thing the agent acts on.
"""

from __future__ import annotations

import asyncio
import io
import itertools
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from neti.core.types import UNREADABLE
from neti.engine import Engine
from neti.gateway.mcp import McpGateway
from neti.gateway.stdio import StdioUpstream, serve_stdio
from neti.preflight import Preflight
from neti.store.jsonl import JsonlSink
from tests.e2e import worlds
from tests.integration.test_inventory import EXAMPLE

DELETE = "DELETE FROM users WHERE org = 'acme'"

_CALL_IDS = itertools.count(1)

# A child process that answers anything it is asked. Reused from the stdio transport tests: the
# point of driving a real subprocess is that a seam which forwards when it should block is caught by
# the server *replying*, not by an assertion we remembered to write.
SERVER = (
    "import sys, json\n"
    "for line in sys.stdin:\n"
    "    msg = json.loads(line)\n"
    "    if msg.get('id') is None:\n"
    "        continue\n"
    "    body = {'content': [{'type': 'text', 'text': 'ran'}]}\n"
    "    print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': body}), flush=True)\n"
)


@dataclass(frozen=True)
class Outcome:
    """What every seam is reduced to, so they can be compared at all."""

    verdict: str
    """`allow`, `confirm` or `block` — normalised out of each runtime's own vocabulary."""

    magnitude: int | None
    sentence: str
    """The denial handed back to the model. Empty when the call proceeds."""


@dataclass(frozen=True)
class Case:
    name: str
    tool: str
    args: dict[str, Any]
    verdict: str

    world: str = "entra"
    """Which policy and resolver set this call is made against. See `tests/e2e/worlds.py`.

    Defaulted, so the five original entra rows below read exactly as they did when the only world
    there was was Entra."""

    magnitude: int | None = None
    """What every seam must agree the number is, for the rows where there is one.

    `None` covers the unresolved rows, the ungated rows, and `entra:allowed` — whose magnitude the
    original table deliberately did not pin because the assertion it cared about was silence."""


# The five original rows, byte for byte. `examples/entra.yaml` under enforce, with magnitudes from
# the synthetic tenant, so every seam resolves identical numbers and any difference is the seam's
# own.
ENTRA_CASES = [
    Case("blocked", "remove_group_members", {"group": "g-eng-all"}, "block", magnitude=41_203),
    Case("allowed", "send_email", {"to": "g-team"}, "allow"),
    Case("unsizeable", "remove_group_members", {"group": "not-a-group-at-all"}, "block"),
    Case("ungated", "read_documentation", {"topic": "anything"}, "allow"),
    # The same tool arriving with an MCP server prefix, which is how Claude Code names tools from
    # an MCP server. It must hit the same policy entry rather than falling through as unknown.
    Case("mcp-prefixed", "mcp__entra__remove_group_members", {"group": "g-eng-all"}, "block"),
    # A gated tool called with the gated argument missing, and with it explicitly null. Both are
    # the failure mode that looks most like success: nothing for the policy to point at, so a gate
    # that reasoned "no target, no problem" would pass the call. The declared `on_unresolved` owns
    # this, and it has to own it on every seam — an adapter that reduces a malformed call to an
    # empty one reaches the same place only by accident.
    Case("no-args", "remove_group_members", {}, "block"),
    Case("null-arg", "remove_group_members", {"group": None}, "block"),
]

# The four resolver families that had never crossed a seam boundary. Placeholders in the arguments
# are filled from the fixture tree — see `worlds.render`.
LOCAL_CASES = [
    # --- fs.paths: what a coding agent does all day, and the case where one short string is the
    # whole repository.
    Case("blocked", "Glob", {"pattern": "{tree}/**/*.txt"}, "block", "fs", 30),
    Case("allowed", "Read", {"file_path": "{tree}/f0.txt"}, "allow", "fs"),
    Case("unsizeable", "directory_tree", {"path": "{missing}"}, "block", "fs"),
    Case("mcp-prefixed", "mcp__fs__directory_tree", {"path": "{tree}"}, "block", "fs", 30),
    # --- db.rows. `bounded` is the important one: every result this resolver produces is a floor,
    # so a resolved statement *under* its ceiling still cannot be allowed and takes `on_unbounded`.
    # That branch is the normal case for a database and no seam test had ever driven it.
    Case("blocked", "execute_sql", {"sql": DELETE}, "block", "db", 400),
    Case("bounded", "query", {"sql": DELETE}, "confirm", "db", 400),
    Case("unsizeable", "execute_sql", {"sql": "TRUNCATE TABLE users"}, "block", "db"),
    Case("ungated", "read_schema", {"table": "users"}, "allow", "db"),
    # --- storage.objects
    Case("blocked", "delete_objects", {"uri": "s3://backups/prod/"}, "block", "storage", 1_200),
    Case("allowed", "delete_objects", {"uri": "s3://backups/scratch/"}, "allow", "storage"),
    Case("unsizeable", "delete_objects", {"uri": "/var/lib/data"}, "block", "storage"),
    # --- terraform.destroy. `unsizeable` is the state file: same command, same shape of JSON, no
    # `resource_changes`. Reporting 0 there would be the worst available wrong answer.
    Case("blocked", "terraform_apply", {"plan": "{plan}"}, "block", "terraform", 7),
    Case("allowed", "terraform_apply", {"plan": "{small_plan}"}, "allow", "terraform"),
    Case("unsizeable", "terraform_apply", {"plan": "{state}"}, "block", "terraform"),
]

CASES = [*ENTRA_CASES, *LOCAL_CASES]


def case_id(case: Case) -> str:
    """`fs:blocked`, `db:bounded`. Every world has a `blocked` row, so the world has to be in the
    id or a failure names a case that five of them share."""
    return f"{case.world}:{case.name}"


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> worlds.Fixtures:
    """The on-disk artefacts, built once.

    Read-only, and shared across the whole table: rebuilding a thirty-file tree and a seeded sqlite
    database for each of ~100 parametrised invocations would be thousands of writes buying nothing.
    The record files each driver writes go to a per-test `tmp_path`, deliberately outside this
    tree — a record landing inside it would change what `fs.paths` counts partway through the table.
    """
    return worlds.build_fixtures(tmp_path_factory.mktemp("seams") / "world")


@pytest.fixture(autouse=True)
def _database_url(monkeypatch: pytest.MonkeyPatch, fixtures: worlds.Fixtures) -> None:
    """`db.rows` reaches the fixture database through the shipped `EnvCountRunner`, not an injected
    one, so the `sqlite:///` parsing and the read-only open are exercised end to end."""
    monkeypatch.setenv("NETI_DATABASE_URL", f"sqlite:///{fixtures.db}")


def world_for(case: Case, fixtures: worlds.Fixtures) -> worlds.World:
    if case.world == "entra":
        return worlds.build_world("entra", fixtures, config=EXAMPLE)
    return worlds.build_world(case.world, fixtures)


def build_engine(world: worlds.World) -> Engine:
    """The world's engine, not a new one — see `World.engine`, where the session tally lives."""
    return world.engine()


def build_preflight(world: worlds.World, tmp_path: Path, approver: Any = None) -> Preflight:
    """The in-process gate, optionally with a control plane behind it.

    Every SDK adapter reaches approvals only through here, so an approver that did not arrive
    would make `CONFIRM` a flat denial on those runtimes while the other four asked a human — the
    paid tier silently worth less depending on which framework somebody chose.
    """
    return Preflight(
        engine=world.engine(),
        sink=JsonlSink(tmp_path / "records.ndjson"),
        approver=approver,
    )


# ---------------------------------------------------------------------------- the seam drivers
#
# Each takes a case and returns an Outcome. Everything runtime-specific lives here; the test below
# knows nothing about JSON-RPC, hook protocols or guardrail objects.


def via_preflight(world: worlds.World, tmp_path: Path, case: Case, approver: Any = None) -> Outcome:
    verdict = build_preflight(world, tmp_path, approver).check(case.tool, case.args)
    # `proceeds`, not `verdict`. Every other seam reports what *happened* — the tool ran or it did
    # not — and `Verdict.verdict` is the decision that was reached before a human answered. A
    # granted approval leaves it reading `confirm` while the call goes through, so comparing that
    # field would make this seam look like the odd one out when it is behaving identically.
    return Outcome(
        verdict="allow" if verdict.proceeds else verdict.verdict,
        magnitude=verdict.payload.get("resolved"),
        sentence=verdict.message,
    )


def via_hook(world: worlds.World, tmp_path: Path, case: Case, approver: Any = None) -> Outcome:
    from neti.adapters.claude_code import run_hook

    out = run_hook(
        build_engine(world),
        {"hook_event_name": "PreToolUse", "tool_name": case.tool, "tool_input": case.args},
        approver=approver,
    )
    if not out:
        # A pass says nothing at all, so the permission rules the operator already configured keep
        # working. Absence of output *is* the allow.
        return Outcome("allow", None, "")
    specific = out["hookSpecificOutput"]
    decision = {"deny": "block", "ask": "confirm"}[specific["permissionDecision"]]
    return Outcome(
        verdict=decision,
        magnitude=specific["neti"].get("resolved"),
        sentence=specific["permissionDecisionReason"],
    )


def via_mcp_http(world: worlds.World, tmp_path: Path, case: Case, approver: Any = None) -> Outcome:
    class Upstream:
        def send(self, message: dict[str, Any], session_id: str | None) -> dict[str, Any] | None:
            return {"jsonrpc": "2.0", "id": message["id"], "result": {"content": []}}

    gateway = McpGateway(engine=build_engine(world), upstream=Upstream(), approver=approver)
    response = gateway.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": case.tool, "arguments": case.args},
        }
    )
    return _from_mcp(response)


def via_mcp_stdio(world: worlds.World, tmp_path: Path, case: Case, approver: Any = None) -> Outcome:
    """A real child process over a real pipe, not an in-process fake."""
    upstream = StdioUpstream([sys.executable, "-c", SERVER])
    gateway = McpGateway(engine=build_engine(world), upstream=upstream, approver=approver)
    out = io.StringIO()
    line = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": case.tool, "arguments": case.args},
        }
    )
    try:
        serve_stdio(gateway, upstream=upstream, stdin=io.StringIO(line + "\n"), stdout=out)
    finally:
        upstream.close()
    responses = [json.loads(ln) for ln in out.getvalue().splitlines() if ln.strip()]
    return _from_mcp(responses[0] if responses else None)


def _from_mcp(response: dict[str, Any] | None) -> Outcome:
    """A denial over MCP is a tool *result* with `isError`, never a protocol error — a protocol
    error would be retried or would crash the client instead of being read by the model."""
    assert response is not None, "the gate must answer every request it was given an id for"
    result = response.get("result") or {}
    if not result.get("isError"):
        return Outcome("allow", None, "")
    text = result["content"][0]["text"]
    payload = result.get("_meta", {}).get("neti", {})
    return Outcome(
        verdict=payload.get("verdict", "block"), magnitude=payload.get("resolved"), sentence=text
    )


def via_anthropic(world: worlds.World, tmp_path: Path, case: Case, approver: Any = None) -> Outcome:
    from anthropic.lib.tools import beta_tool

    from neti.adapters.anthropic_tools import gate_tool

    ran: list[bool] = []

    def make(name: str) -> Any:
        @beta_tool
        def tool(**kwargs: Any) -> str:
            """Do the thing."""
            ran.append(True)
            return "ran"

        tool.name = name
        return tool

    gated = gate_tool(build_preflight(world, tmp_path, approver), make(case.tool))
    result = gated.call(case.args)
    if ran:
        return Outcome("allow", None, "")
    return Outcome(_verdict_of(str(result)), _magnitude_of(str(result)), str(result))


def via_openai_agents(
    world: worlds.World, tmp_path: Path, case: Case, approver: Any = None
) -> Outcome:
    from agents import Agent
    from agents.tool_context import ToolContext
    from agents.tool_guardrails import ToolInputGuardrailData

    from neti.adapters.openai_agents import verdict_for

    data = ToolInputGuardrailData(
        context=ToolContext(
            context=None,
            tool_name=case.tool,
            # A fresh id per call, because that is what the SDK does — `tool_call_id` identifies one
            # invocation. It was pinned to `"call_1"` here, which made every driver invocation look
            # like the same call to anything downstream keyed on it, and hid the fact that the
            # adapter was passing this as the *session* id.
            tool_call_id=f"call_{next(_CALL_IDS)}",
            tool_arguments=json.dumps(case.args),
        ),
        agent=Agent(name="test"),
    )
    out = verdict_for(build_preflight(world, tmp_path, approver), data)
    if out.behavior["type"] == "allow":
        return Outcome("allow", None, "")
    payload = out.output_info["neti"]
    return Outcome(payload["verdict"], payload.get("resolved"), out.behavior["message"])


def via_langchain(world: worlds.World, tmp_path: Path, case: Case, approver: Any = None) -> Outcome:
    from langchain_core.tools import StructuredTool

    from neti.adapters.langchain_tools import gate_tool

    ran: list[bool] = []

    def run(**kwargs: Any) -> str:
        ran.append(True)
        return "ran"

    inner = StructuredTool.from_function(
        func=run, name=case.tool.replace("__", "_"), description="Do the thing."
    )
    # The wrapper reads `self.name`, so the policy sees the original name including any MCP prefix.
    # LangChain rejects `__` in a tool name, hence the sanitised inner name and the restore here.
    gated = gate_tool(build_preflight(world, tmp_path, approver), inner).model_copy(
        update={"name": case.tool}
    )
    result = gated.invoke(case.args)
    if ran:
        return Outcome("allow", None, "")
    return Outcome(_verdict_of(str(result)), _magnitude_of(str(result)), str(result))


def _verdict_of(sentence: str) -> str:
    """Seams that hand back only a sentence still have to be classified, and the sentence is the
    contract. `blocked` and `needs confirmation` are the two openings the gate ever writes."""
    if "needs confirmation" in sentence:
        return "confirm"
    return "block"


def _magnitude_of(sentence: str) -> int | None:
    """Pulled back out of the sentence for the seams that carry no structured payload.

    Doing it this way is the point rather than a shortcut: it asserts the number the *model* sees,
    which is the number that makes it narrow its scope, rather than one sitting in a side channel
    the model never reads.
    """
    import re

    match = re.search(r"resolves to ([\d,]+)", sentence)
    return int(match.group(1).replace(",", "")) if match else None


def via_google_adk(
    world: worlds.World, tmp_path: Path, case: Case, approver: Any = None
) -> Outcome:
    """ADK's plugin callback: `None` proceeds, a non-empty dict replaces the call."""
    from neti.adapters.google_adk import neti_plugin

    plugin = neti_plugin(build_preflight(world, tmp_path, approver))
    tool = SimpleNamespace(name=case.tool)
    out = asyncio.run(
        plugin.before_tool_callback(tool=tool, tool_args=dict(case.args), tool_context=None)
    )
    if out is None:
        return Outcome("allow", None, "")
    payload = out["neti"]
    return Outcome(payload["verdict"], payload.get("resolved"), out["error"])


def via_pydantic_ai(
    world: worlds.World, tmp_path: Path, case: Case, approver: Any = None
) -> Outcome:
    """`before_tool_execute`, which signals a refusal by raising `ToolFailed` — caught by the
    framework, so the run continues and the model reads the sentence."""
    from pydantic_ai.exceptions import ToolFailed
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

    from neti.adapters.pydantic_ai import neti_hooks

    hooks = neti_hooks(build_preflight(world, tmp_path, approver))
    call = ToolCallPart(tool_name=case.tool, args=dict(case.args), tool_call_id="call_1")

    async def run() -> Outcome:
        try:
            await hooks.before_tool_execute(
                None,
                call=call,
                tool_def=ToolDefinition(name=case.tool),
                args=dict(case.args),
            )
        except ToolFailed as failed:
            text = str(failed.message)
            return Outcome(_verdict_of(text), _magnitude_of(text), text)
        return Outcome("allow", None, "")

    return asyncio.run(run())


def via_crewai(world: worlds.World, tmp_path: Path, case: Case, approver: Any = None) -> Outcome:
    """The hook *pair*, driven the way `crew_agent_executor` drives it.

    CrewAI substitutes a fixed "Tool execution blocked by hook" string when a before-hook returns
    `False`, so the after-hook is what puts the sentence back. Reproducing both halves here is the
    point: a driver that only called the before-hook would report a verdict and never notice that
    the model was being handed no number at all.
    """
    from crewai.hooks import (
        ToolCallHookContext,
        clear_all_tool_call_hooks,
        get_after_tool_call_hooks,
        get_before_tool_call_hooks,
    )

    from neti.adapters.crewai_hooks import install

    clear_all_tool_call_hooks()
    try:
        install(build_preflight(world, tmp_path, approver))

        blocked = False
        for hook in get_before_tool_call_hooks():
            context = ToolCallHookContext(
                tool_name=case.tool, tool_input=dict(case.args), tool=None, agent=None, task=None
            )
            if hook(context) is False:
                blocked = True
        if not blocked:
            return Outcome("allow", None, "")

        text = f"Tool execution blocked by hook. Tool: {case.tool}"
        for hook in get_after_tool_call_hooks():
            context = ToolCallHookContext(
                tool_name=case.tool,
                tool_input=dict(case.args),
                tool=None,
                agent=None,
                task=None,
                tool_result=text,
            )
            replaced = hook(context)
            if isinstance(replaced, str):
                text = replaced
        return Outcome(_verdict_of(text), _magnitude_of(text), text)
    finally:
        clear_all_tool_call_hooks()


def via_autogen(world: worlds.World, tmp_path: Path, case: Case, approver: Any = None) -> Outcome:
    """The wrapped workbench. A denial is a `ToolResult` with `is_error=True`.

    The wrapped workbench answers anything it is asked, for the same reason the stdio driver spawns
    a child process that replies to everything: a seam that forwards a call it should have blocked
    is then caught by the *downstream* answering, rather than by an assertion somebody remembered to
    write. `StaticWorkbench` would return `is_error=True` for a tool it does not know, which reads
    identically to a denial and would have made every allow look like a block.
    """
    from autogen_core.tools import TextResultContent, ToolResult, Workbench

    from neti.adapters.autogen_tools import gate_workbench

    class AnyTool(Workbench):  # type: ignore[misc]
        async def list_tools(self) -> Any:
            return []

        async def call_tool(
            self,
            name: str,
            arguments: Any = None,
            cancellation_token: Any = None,
            call_id: Any = None,
        ) -> Any:
            return ToolResult(name=name, result=[TextResultContent(content="ran")], is_error=False)

        async def start(self) -> None: ...
        async def stop(self) -> None: ...
        async def reset(self) -> None: ...
        async def save_state(self) -> Any:
            return {}

        async def load_state(self, state: Any) -> None: ...

    bench = gate_workbench(build_preflight(world, tmp_path, approver), AnyTool())
    result = asyncio.run(bench.call_tool(case.tool, dict(case.args)))
    if not result.is_error:
        return Outcome("allow", None, "")
    text = str(result.result[0].content)
    return Outcome(_verdict_of(text), _magnitude_of(text), text)


def via_tool_loop(world: worlds.World, tmp_path: Path, case: Case, approver: Any = None) -> Outcome:
    """A hand-written Anthropic or OpenAI tool loop, with its dispatch table wrapped once.

    No framework at all — this is the shape most agents in the world are, and the one seam whose
    coverage depends on the author having substituted their own dict. Driven here as they would use
    it: wrap the table, then call through it exactly as the loop does.
    """
    from neti.adapters.tool_loop import gate_tools

    def ran(**kwargs: Any) -> str:
        return "ran"

    tools = gate_tools(build_preflight(world, tmp_path, approver), {case.tool: ran})
    result = tools[case.tool](**case.args)
    if result == "ran":
        return Outcome("allow", None, "")
    return Outcome(_verdict_of(str(result)), _magnitude_of(str(result)), str(result))


SEAMS = {
    "preflight": via_preflight,
    "tool-loop": via_tool_loop,
    "hook": via_hook,
    "mcp-http": via_mcp_http,
    "mcp-stdio": via_mcp_stdio,
    "anthropic": via_anthropic,
    "openai-agents": via_openai_agents,
    "langchain": via_langchain,
    "google-adk": via_google_adk,
    "pydantic-ai": via_pydantic_ai,
    "crewai": via_crewai,
    "autogen": via_autogen,
}

NEEDS_SDK = {
    "anthropic": "anthropic",
    "openai-agents": "agents",
    "langchain": "langchain_core",
    "google-adk": "google.adk",
    "pydantic-ai": "pydantic_ai",
    "crewai": "crewai",
    "autogen": "autogen_core",
}


def outcome(
    seam: str, fixtures: worlds.Fixtures, tmp_path: Path, case: Case, approver: Any = None
) -> Outcome:
    """Build this case's world, fill in its paths, and drive one seam with it.

    The world is built per call rather than shared, which matters: `Engine` holds the session
    tallies, so one engine shared across the seams would have the last of them see every other
    seam's traffic in the session total and reach a different verdict for that reason alone.
    """
    case = replace(case, args=worlds.render(case.args, fixtures))
    return drive(seam, world_for(case, fixtures), tmp_path, case, approver)


def drive(
    seam: str, world: worlds.World, tmp_path: Path, case: Case, approver: Any = None
) -> Outcome:
    """One seam, against a world the caller owns.

    Split out from `outcome` for the one test that has to drive the *same* world twice: a session
    budget lives on the engine, so two calls only share a session if they share a world.
    """
    module = NEEDS_SDK.get(seam)
    if module:
        pytest.importorskip(module, reason=f"{seam} needs the sdks extra")
    return SEAMS[seam](world, tmp_path, case, approver)


# ---------------------------------------------------------------------------- the invariant


@pytest.mark.parametrize("case", CASES, ids=case_id)
@pytest.mark.parametrize("seam", sorted(SEAMS))
def test_every_seam_reaches_the_same_verdict(
    seam: str, case: Case, fixtures: worlds.Fixtures, tmp_path: Path
) -> None:
    """The door a call came through must not change whether it is allowed."""
    assert outcome(seam, fixtures, tmp_path, case).verdict == case.verdict


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_every_seam_agrees_on_the_magnitude_and_the_sentence(
    case: Case, fixtures: worlds.Fixtures, tmp_path: Path
) -> None:
    """One denial, one owner.

    The sentence is compared byte for byte because it *is* the product's output — it is what the
    model reads and what makes it retry with a narrower target. A seam that rephrases it has changed
    the thing the agent acts on, which no per-adapter test would notice.
    """
    results = {seam: outcome(seam, fixtures, tmp_path, case) for seam in sorted(SEAMS)}

    magnitudes = {seam: out.magnitude for seam, out in results.items() if out.magnitude is not None}
    assert len(set(magnitudes.values())) <= 1, f"seams disagree on the magnitude: {magnitudes}"

    sentences = {seam: out.sentence for seam, out in results.items() if out.sentence}
    assert len(set(sentences.values())) <= 1, "seams disagree on the denial wording:\n" + "\n".join(
        f"  {seam}: {text!r}" for seam, text in sorted(sentences.items())
    )


@pytest.mark.parametrize("case", [c for c in CASES if c.magnitude is not None], ids=case_id)
def test_a_stopped_call_names_its_number_everywhere_it_can(
    case: Case, fixtures: worlds.Fixtures, tmp_path: Path
) -> None:
    """The specific claim the product makes: the model is told *how big*, not merely "no".

    Asserted against the sentence rather than a payload, because a number in a structured field the
    model never reads does not make it narrow its scope. Now over every world rather than over
    41,203 alone — "names the magnitude" was only ever checked for one resolver, and four of them
    could have been handing back a bare refusal.
    """
    for seam in sorted(SEAMS):
        out = outcome(seam, fixtures, tmp_path, case)
        assert f"{case.magnitude:,}" in out.sentence, (
            f"{seam} stopped {case_id(case)} without naming the magnitude: {out.sentence!r}"
        )


@pytest.mark.parametrize("case", [c for c in CASES if c.verdict == "allow"], ids=case_id)
def test_an_allowed_call_says_nothing_at_all(
    case: Case, fixtures: worlds.Fixtures, tmp_path: Path
) -> None:
    """Silence on the happy path is a contract, not an accident: the hook emitting anything at all
    would override the permission rules an operator already configured.

    Every world's allow and every world's ungated row, because the contract is about the *seam*
    and an adapter that narrated a pass would do it regardless of which resolver was behind it.
    """
    for seam in sorted(SEAMS):
        assert outcome(seam, fixtures, tmp_path, case).sentence == ""


def test_the_seam_table_covers_every_shipped_adapter() -> None:
    """The table is only an invariant while it is complete.

    A new adapter added to `neti.adapters` without a row here would be the same hole the first
    three SDK adapters sat in: individually tested, never compared, free to drift.
    """
    import pkgutil

    import neti.adapters

    shipped = {name for _, name, _ in pkgutil.iter_modules(neti.adapters.__path__)}
    covered = {
        "claude_code",
        "tool_loop",
        "anthropic_tools",
        "openai_agents",
        "langchain_tools",
        "google_adk",
        "pydantic_ai",
        "crewai_hooks",
        "autogen_tools",
    }
    assert shipped == covered, (
        f"adapters with no seam-equivalence row: {sorted(shipped - covered)}. "
        "Add a driver above so it cannot drift from the others."
    )


def test_the_seam_table_covers_every_resolver_a_shipped_example_gates() -> None:
    """The same completeness claim on the other axis.

    Ship an example that gates a new resolver and this demands a world for it. That is exactly the
    hole the four local resolvers sat in: `fs.paths`, `db.rows`, `storage.objects` and
    `terraform.destroy` were each well tested at producing a `Resolution`, each driven through the
    stack once by `test_resolver_matrix.py`, and never once compared across the seven doors — so a
    seam could have disagreed about any of them indefinitely.

    Read off `examples/` rather than off the registry on purpose. A resolver nobody ships an example
    for is one no operator can be gating yet; a resolver we hand people a policy for is one the
    seams have to agree about.
    """
    from neti.config.policy import Policy, load_policy

    shipped = {
        gate.resolver
        for path in sorted(Path("examples").glob("*.yaml"))
        for tool in load_policy(str(path)).tools.values()
        for gate in tool.gate.values()
    }
    # Read off the world policies rather than off a built world: this needs no fixture tree, and a
    # world declared but never driven should still fail loudly rather than count as coverage.
    declared = [Policy.model_validate(spec) for spec in worlds.POLICIES.values()]
    declared.append(load_policy(EXAMPLE))
    covered = {
        gate.resolver
        for policy in declared
        for tool in policy.tools.values()
        for gate in tool.gate.values()
    }
    assert shipped <= covered, (
        f"resolvers a shipped example gates with no world here: {sorted(shipped - covered)}. "
        "Add one to `tests/e2e/worlds.py` so every seam gets driven against it."
    )


# ---------------------------------------------------------------------------- approvals


# A 500-member group: confirm above 50, block above 500. Squarely a CONFIRM, which is the only
# verdict a control plane can act on.
NEEDS_A_HUMAN = Case("confirming", "send_email", {"to": "g-dept"}, "confirm")


def approver(answer: str) -> Any:
    from neti.approvals import ApprovalState
    from tests.integration.test_approvals import FakeApprover

    return FakeApprover(answer=ApprovalState(answer))


@pytest.mark.parametrize("seam", sorted(SEAMS))
def test_a_confirm_stops_the_call_on_every_seam_when_nobody_can_be_asked(
    seam: str, fixtures: worlds.Fixtures, tmp_path: Path
) -> None:
    """The free tier, and the behaviour every paid install degrades to.

    With no control plane a `CONFIRM` means "this does not proceed without a human" and there is no
    human, so it stops. That has to be true on every seam or the tier boundary is not a
    boundary — an install would be quietly more permissive on whichever runtime forgot.
    """
    assert outcome(seam, fixtures, tmp_path, NEEDS_A_HUMAN).verdict == "confirm"


@pytest.mark.parametrize("seam", sorted(SEAMS))
def test_a_granted_approval_lets_the_call_through_on_every_seam(
    seam: str, fixtures: worlds.Fixtures, tmp_path: Path
) -> None:
    """`test_a_grant_is_honoured_identically_on_all_three_seams` extended to seven.

    That test predates the Anthropic, OpenAI Agents and LangChain adapters, and those reach the
    control plane by a different route — through `Preflight` rather than through `Gatekeeper`
    directly. So the runtimes most people use were the ones with no assertion that approvals work
    on them at all: a paying customer on LangChain could have been getting flat denials where a
    customer on MCP got a human, and nothing would have said so.
    """
    assert outcome(seam, fixtures, tmp_path, NEEDS_A_HUMAN, approver("granted")).verdict == "allow"


@pytest.mark.parametrize("seam", sorted(SEAMS))
def test_a_denied_approval_stops_the_call_on_every_seam(
    seam: str, fixtures: worlds.Fixtures, tmp_path: Path
) -> None:
    """The direction that matters if the plumbing is wrong.

    A grant arriving where it should not is the failure a reviewer will ask about, so the refusal
    path is asserted separately rather than inferred from the grant working.
    """
    assert outcome(seam, fixtures, tmp_path, NEEDS_A_HUMAN, approver("denied")).verdict != "allow"


@pytest.mark.parametrize("seam", sorted(SEAMS))
def test_an_approver_can_never_make_a_block_proceed_on_any_seam(
    seam: str, fixtures: worlds.Fixtures, tmp_path: Path
) -> None:
    """The tier boundary's load-bearing claim: a control plane can only ever be *more* permissive
    about a `CONFIRM`, and a `BLOCK` is never put to a human at all.

    If any seam escalated a block, paying for approvals would buy a way around a declared ceiling —
    which is the opposite of what the product is.
    """
    blocked = next(c for c in CASES if c.name == "blocked")
    assert outcome(seam, fixtures, tmp_path, blocked, approver("granted")).verdict == "block"


# ---------------------------------------------------------------------------- unreadable arguments


@pytest.mark.parametrize("seam", ["anthropic", "openai-agents"])
def test_an_unreadable_payload_is_preserved_not_erased(
    seam: str, fixtures: worlds.Fixtures, tmp_path: Path
) -> None:
    """The two seams that can be handed something which is not a dict at all.

    Both already reached the right *verdict*, and that is exactly why this drifted unnoticed for so
    long: an absent gated argument and an unreadable one both resolve to `None` and take the
    declared `on_unresolved`, so no verdict assertion anywhere could tell the two adapters apart.

    The record could. The OpenAI adapter kept a truncated copy of what arrived; the Anthropic one
    substituted `{}`, which states that a call was made carrying no arguments. That is not what
    happened, and of the two situations it is the less alarming one — an auditor reading the chain
    had no way to see that a payload the gate could not parse had turned up at all.
    """
    case = Case("unreadable", "remove_group_members", {}, "block")
    world = world_for(case, fixtures)
    # A bare string where the runtime's contract promises an object.
    raw: Any = "g-eng-all"

    if seam == "anthropic":
        pytest.importorskip("anthropic", reason="the sdks extra is not installed")
        from anthropic.lib.tools import beta_tool

        from neti.adapters.anthropic_tools import gate_tool

        @beta_tool
        def tool(**kwargs: Any) -> str:
            """Do the thing."""
            raise AssertionError("the gate let an unreadable call through")

        tool.name = case.tool
        preflight = build_preflight(world, tmp_path)
        gate_tool(preflight, tool).call(raw)
    else:
        pytest.importorskip("agents", reason="the sdks extra is not installed")
        from agents import Agent
        from agents.tool_context import ToolContext
        from agents.tool_guardrails import ToolInputGuardrailData

        from neti.adapters.openai_agents import verdict_for

        preflight = build_preflight(world, tmp_path)
        verdict_for(
            preflight,
            ToolInputGuardrailData(
                context=ToolContext(
                    context=None,
                    tool_name=case.tool,
                    tool_call_id=f"call_{next(_CALL_IDS)}",
                    tool_arguments=raw,
                ),
                agent=Agent(name="test"),
            ),
        )

    preflight.close()
    written = (tmp_path / "records.ndjson").read_text(encoding="utf-8")
    assert UNREADABLE in written, (
        f"{seam} erased the payload it could not read; the record cannot distinguish it from a "
        "call that carried no arguments at all"
    )


# ---------------------------------------------------------------------------- session budgets


# One file. Resolves to 1 object, passes any per-call ceiling that could be written, and two of them
# exceed a declared session total of 1.
BUDGETED = Case("budgeted", "Read", {"file_path": "{tree}/f0.txt"}, "allow", "budget")


@pytest.mark.parametrize("seam", sorted(SEAMS))
def test_a_session_budget_accumulates_across_calls_on_every_seam(
    seam: str, fixtures: worlds.Fixtures, tmp_path: Path
) -> None:
    """SCOPE.md NC-01, and whether its only mitigation is actually wired on each runtime.

    Per-call resolution is structurally blind to four thousand small calls — each resolves to 1 and
    passes every ceiling — and the document is explicit that *only a declared session budget* sees
    it. A budget that fails to accumulate is therefore not a degraded feature; it is the single
    countermeasure to the product's largest declared blind spot, switched off.

    Whether two calls share a session is decided by what each adapter passes as `session_id`, and
    that is per-adapter code with nothing comparing it. The OpenAI Agents adapter passed the SDK's
    `tool_call_id` — which is unique to one call — so every call opened a fresh tally and the total
    was permanently 1. Ten thousand deletions would each have been counted as the first.
    """
    world = world_for(BUDGETED, fixtures)
    case = replace(BUDGETED, args=worlds.render(BUDGETED.args, fixtures))

    assert drive(seam, world, tmp_path, case).verdict == "allow", (
        "the first call is under the budget and must pass"
    )
    second = drive(seam, world, tmp_path, case)
    assert second.verdict == "block", (
        f"{seam} did not accumulate: the session total is not carrying across calls, so a declared "
        "budget cannot fire on this runtime"
    )
    assert "session" in second.sentence, (
        "a budget denial must say the session total is the problem, not the call — the remedies "
        f"are different: {second.sentence!r}"
    )


def test_a_pending_approval_reads_the_same_on_every_seam(
    fixtures: worlds.Fixtures, tmp_path: Path
) -> None:
    """The third approval state, and the one nobody was checking.

    Granted and denied each had a row above. *Pending* — a human has been asked and has not answered
    yet — had none, and it is the state a real control plane spends most of its time in, because a
    person takes minutes and a tool call takes seconds.

    Two things were wrong under that gap, and neither could be seen without this row. The hook and
    the MCP gateway each carried their own copy of the sentence and had drifted: the gateway told
    the model to *retry this exact call once it is granted*, which is the entire reason for naming
    an approval id, and the hook stopped at "is pending for this call" and left the agent with
    nothing to act on. And `Preflight` had no copy at all, so on the three SDK seams a pending
    approval arrived as a flat "needs confirmation" — no id, no sign a human had been asked, and
    nothing to retry — which made the paid tier worth measurably less to anyone on LangChain.
    """
    results = {
        seam: outcome(seam, fixtures, tmp_path, NEEDS_A_HUMAN, approver("pending"))
        for seam in sorted(SEAMS)
    }

    sentences = {seam: out.sentence for seam, out in results.items() if out.sentence}
    assert len(set(sentences.values())) == 1, (
        "seams disagree on how a pending approval reads:\n"
        + "\n".join(f"  {seam}: {text!r}" for seam, text in sorted(sentences.items()))
    )

    one = next(iter(sentences.values()))
    assert "is pending for this call" in one
    assert "Retry this exact call once it is granted" in one, (
        "a pending approval that does not tell the agent to retry the identical call is an "
        "approval it cannot collect: the grant is bound to that exact call under that exact policy"
    )
