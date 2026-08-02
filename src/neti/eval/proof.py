"""`neti prove` — drive one call through every door that is actually here, and seal the evidence.

The claim this product makes about runtimes is easy to say and hard to believe: *whichever way your
agent calls a tool, the answer is the same*. Eleven adapters is a number in a README. This runs the
same call through each door that is installed on the machine you are standing on, shows the verdict,
the magnitude and the sentence each one produced, and then verifies the hash chain they all wrote
into. Somebody watching can check every line of it.

**Two rules, and they are the whole reason this is worth reading.**

*Driven and cited are different, and never mixed.* The four SDK adapters need their SDKs, which the
wheel deliberately does not ship — `pip install neti` must not pull in four agent frameworks. So a
seam whose SDK is absent is reported as **not driven here**, naming the test that does drive it.
What this command must never do is print a row that looks measured for a door it did not open.

*The proof is the chain, not the table.* Any program can print eleven identical lines. Every
decision below is sealed into one hash chain, `verify_chain` recomputes it here, and the same file
`neti verify` reads is the artefact. The table is the story; the chain is the evidence.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from neti.config.policy import load_policy
from neti.core.record import verify_chain
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.synthetic import SyntheticTenant, default_tenant
from neti.gateway.mcp import McpGateway
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from neti.store.jsonl import JsonlSink, chain_head, read_records

__all__ = ["Proof", "SeamProof", "format_proof", "run_proof"]

_CRED = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")

# The call every door is asked about. Chosen because its answer is unmistakable: 41,203 people is
# not a number anybody squints at, and every seam has to produce that same number or fail here.
TOOL = "remove_group_members"
ARGS: dict[str, Any] = {"group": "g-eng-all"}

# A child process that answers anything. `mcp-stdio` spawns this for real, so a gate that forwarded
# a call it should have stopped is caught by the child *replying* rather than by an assertion.
_CHILD = (
    "import sys, json\n"
    "for line in sys.stdin:\n"
    "    msg = json.loads(line)\n"
    "    if msg.get('id') is None:\n"
    "        continue\n"
    "    body = {'content': [{'type': 'text', 'text': 'ran'}]}\n"
    "    print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': body}), flush=True)\n"
)

# Which import each seam needs before it can be driven at all, and what proves it when it cannot be.
# `None` means the seam needs nothing beyond the wheel.
NEEDS: dict[str, str | None] = {
    "preflight": None,
    "hook": None,
    "mcp-http": None,
    "mcp-stdio": None,
    "anthropic": "anthropic",
    "openai-agents": "agents",
    "langchain": "langchain_core",
    "crewai": "crewai",
    "pydantic-ai": "pydantic_ai",
    "autogen": "autogen_core",
    "google-adk": "google.adk",
}

WHAT: dict[str, str] = {
    "preflight": "a tool loop you wrote yourself",
    "hook": "Claude Code's PreToolUse hook",
    "mcp-http": "neti gate, in front of a remote MCP server",
    "mcp-stdio": "neti gate, in front of a local MCP server",
    "anthropic": "the Anthropic tool runner",
    "openai-agents": "the OpenAI Agents SDK",
    "langchain": "LangChain and LangGraph",
    "crewai": "CrewAI",
    "pydantic-ai": "Pydantic AI",
    "autogen": "AutoGen",
    "google-adk": "Google ADK",
}

PROVEN_BY = "tests/e2e/test_seam_equivalence.py"


@dataclass(frozen=True)
class SeamProof:
    """One door, and what came back out of it."""

    seam: str
    what: str

    driven: bool
    """True when this machine actually opened the door. False is not a failure — it is the honest
    report that the SDK is not installed here, and it must never be rendered as though it were a
    measurement."""

    verdict: str | None = None
    magnitude: int | None = None
    sentence: str = ""
    missing: str | None = None
    """The import that was absent, when `driven` is False."""


@dataclass
class Proof:
    tool: str
    args: dict[str, Any]
    seams: list[SeamProof] = field(default_factory=list)
    records_path: Path | None = None
    policy_path: str = ""
    records: int = 0
    chain_ok: bool = False
    head: str | None = None

    @property
    def driven(self) -> list[SeamProof]:
        return [s for s in self.seams if s.driven]

    @property
    def cited(self) -> list[SeamProof]:
        return [s for s in self.seams if not s.driven]

    @property
    def agreed(self) -> bool:
        """Every door that was opened returned the same verdict, magnitude and sentence.

        Byte-for-byte on the sentence, because the sentence is what the model reads and what makes
        it retry with a narrower target. A seam that rephrased it has changed the thing the agent
        acts on, and that is not agreement.
        """
        answers = {(s.verdict, s.magnitude, s.sentence) for s in self.driven}
        return len(answers) == 1


# --------------------------------------------------------------------------- the doors
#
# One driver per seam, each reduced to `(verdict, magnitude, sentence)`. They take a built `Engine`
# rather than building one, so every decision below lands in the same chain — which is what makes
# the verification at the end mean anything.


def _available(module: str | None) -> bool:
    if module is None:
        return True
    from importlib.util import find_spec

    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _preflight(engine: Engine, sink: Any) -> tuple[str, int | None, str]:
    from neti.preflight import Preflight

    verdict = Preflight(engine=engine, sink=sink).check(TOOL, dict(ARGS))
    return verdict.verdict, verdict.payload.get("resolved"), verdict.message


def _hook(engine: Engine, sink: Any) -> tuple[str, int | None, str]:
    from neti.adapters.claude_code import run_hook

    event = {"hook_event_name": "PreToolUse", "tool_name": TOOL, "tool_input": ARGS}
    out = run_hook(engine, event, sink)
    specific = out["hookSpecificOutput"]
    decision = {"deny": "block", "ask": "confirm"}[specific["permissionDecision"]]
    return decision, specific["neti"].get("resolved"), specific["permissionDecisionReason"]


class _Upstream:
    """An MCP server that would have answered. It must never be reached."""

    reached = False

    def send(self, message: dict[str, Any], session_id: str | None) -> dict[str, Any] | None:
        self.reached = True
        return {"jsonrpc": "2.0", "id": message["id"], "result": {"content": []}}


def _mcp(engine: Engine, upstream: Any, sink: Any) -> tuple[str, int | None, str]:
    gateway = McpGateway(engine=engine, upstream=upstream, sink=sink)
    response = gateway.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": TOOL, "arguments": ARGS},
        }
    )
    assert response is not None
    result = response["result"]
    payload = result.get("_meta", {}).get("neti", {})
    return payload.get("verdict", "block"), payload.get("resolved"), result["content"][0]["text"]


def _mcp_http(engine: Engine, sink: Any) -> tuple[str, int | None, str]:
    return _mcp(engine, _Upstream(), sink)


def _mcp_stdio(engine: Engine, sink: Any) -> tuple[str, int | None, str]:
    """A real child process on a real pipe, spawned here and killed here."""
    from neti.gateway.stdio import StdioUpstream

    upstream = StdioUpstream([sys.executable, "-c", _CHILD])
    try:
        return _mcp(engine, upstream, sink)
    finally:
        upstream.close()


def _via_preflight_adapter(engine: Engine, sink: Any, drive: Any) -> tuple[str, int | None, str]:
    """The SDK adapters all gate through `Preflight`, so they share one shape."""
    from neti.preflight import Preflight

    driven: tuple[str, int | None, str] = drive(Preflight(engine=engine, sink=sink))
    return driven


def _anthropic(engine: Engine, sink: Any) -> tuple[str, int | None, str]:
    from anthropic.lib.tools import beta_tool

    from neti.adapters.anthropic_tools import gate_tool

    @beta_tool
    def tool(**kwargs: Any) -> str:
        """Do the thing."""
        raise AssertionError("the gate let the call through")

    tool.name = TOOL

    def drive(pf: Any) -> tuple[str, int | None, str]:
        text = str(gate_tool(pf, tool).call(dict(ARGS)))
        return _classify(text)

    return _via_preflight_adapter(engine, sink, drive)


def _openai_agents(engine: Engine, sink: Any) -> tuple[str, int | None, str]:
    import json as _json

    from agents import Agent
    from agents.tool_context import ToolContext
    from agents.tool_guardrails import ToolInputGuardrailData

    from neti.adapters.openai_agents import verdict_for

    def drive(pf: Any) -> tuple[str, int | None, str]:
        data = ToolInputGuardrailData(
            context=ToolContext(
                context=None,
                tool_name=TOOL,
                tool_call_id="call_1",
                tool_arguments=_json.dumps(ARGS),
            ),
            agent=Agent(name="proof"),
        )
        out = verdict_for(pf, data)
        payload = out.output_info["neti"]
        return payload["verdict"], payload.get("resolved"), out.behavior["message"]

    return _via_preflight_adapter(engine, sink, drive)


def _langchain(engine: Engine, sink: Any) -> tuple[str, int | None, str]:
    from langchain_core.tools import StructuredTool

    from neti.adapters.langchain_tools import gate_tool

    def run(**kwargs: Any) -> str:
        raise AssertionError("the gate let the call through")

    inner = StructuredTool.from_function(func=run, name=TOOL, description="Do the thing.")

    def drive(pf: Any) -> tuple[str, int | None, str]:
        return _classify(str(gate_tool(pf, inner).invoke(dict(ARGS))))

    return _via_preflight_adapter(engine, sink, drive)


def _crewai(engine: Engine, sink: Any) -> tuple[str, int | None, str]:
    from crewai.hooks import (
        ToolCallHookContext,
        clear_all_tool_call_hooks,
        get_after_tool_call_hooks,
        get_before_tool_call_hooks,
    )

    from neti.adapters.crewai_hooks import install
    from neti.preflight import Preflight

    clear_all_tool_call_hooks()
    try:
        install(Preflight(engine=engine, sink=sink))
        # `tool`, `agent` and `task` are the crew's own objects and the gate reads none of them —
        # it needs the name and the arguments. Building a whole crew to prove a verdict would be
        # proving the crew.
        context = ToolCallHookContext(
            tool_name=TOOL,
            tool_input=dict(ARGS),
            tool=None,  # type: ignore[arg-type]
            agent=None,
            task=None,
        )
        blocked = any(hook(context) is False for hook in get_before_tool_call_hooks())
        text = f"Tool execution blocked by hook. Tool: {TOOL}" if blocked else ""
        for hook in get_after_tool_call_hooks():
            after = ToolCallHookContext(
                tool_name=TOOL,
                tool_input=dict(ARGS),
                tool=None,  # type: ignore[arg-type]
                agent=None,
                task=None,
                tool_result=text,
            )
            replaced = hook(after)
            if isinstance(replaced, str):
                text = replaced
        return _classify(text)
    finally:
        clear_all_tool_call_hooks()


def _pydantic_ai(engine: Engine, sink: Any) -> tuple[str, int | None, str]:
    import asyncio

    from pydantic_ai.exceptions import ToolFailed
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

    from neti.adapters.pydantic_ai import neti_hooks
    from neti.preflight import Preflight

    hooks = neti_hooks(Preflight(engine=engine, sink=sink))

    async def go() -> tuple[str, int | None, str]:
        try:
            await hooks.before_tool_execute(
                None,
                call=ToolCallPart(tool_name=TOOL, args=dict(ARGS), tool_call_id="c1"),
                tool_def=ToolDefinition(name=TOOL),
                args=dict(ARGS),
            )
        except ToolFailed as failed:
            return _classify(str(failed.message))
        raise AssertionError("the gate let the call through")

    return asyncio.run(go())


def _autogen(engine: Engine, sink: Any) -> tuple[str, int | None, str]:
    import asyncio

    from autogen_core.tools import StaticWorkbench

    from neti.adapters.autogen_tools import gate_workbench
    from neti.preflight import Preflight

    bench = gate_workbench(Preflight(engine=engine, sink=sink), StaticWorkbench(tools=[]))
    result = asyncio.run(bench.call_tool(TOOL, dict(ARGS)))
    return _classify(str(result.result[0].content))


def _google_adk(engine: Engine, sink: Any) -> tuple[str, int | None, str]:
    import asyncio
    from types import SimpleNamespace

    from neti.adapters.google_adk import neti_plugin
    from neti.preflight import Preflight

    plugin = neti_plugin(Preflight(engine=engine, sink=sink))
    out = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name=TOOL), tool_args=dict(ARGS), tool_context=None
        )
    )
    assert out is not None, "the gate let the call through"
    payload = out["neti"]
    return payload["verdict"], payload.get("resolved"), out["error"]


def _classify(sentence: str) -> tuple[str, int | None, str]:
    """Seams that hand back only a sentence still have to be reduced to the same triple.

    The magnitude is read back out of the sentence deliberately rather than from a side channel:
    that is the number the *model* sees, and a proof that quoted a figure the model never reads
    would be proving the wrong thing.
    """
    import re

    verdict = "confirm" if "needs confirmation" in sentence else "block"
    found = re.search(r"resolves to ([\d,]+)", sentence)
    return verdict, int(found.group(1).replace(",", "")) if found else None, sentence


DRIVERS: dict[str, Any] = {
    "preflight": _preflight,
    "hook": _hook,
    "mcp-http": _mcp_http,
    "mcp-stdio": _mcp_stdio,
    "anthropic": _anthropic,
    "openai-agents": _openai_agents,
    "langchain": _langchain,
    "crewai": _crewai,
    "pydantic-ai": _pydantic_ai,
    "autogen": _autogen,
    "google-adk": _google_adk,
}


# --------------------------------------------------------------------------- the run


def run_proof(policy_path: str, records: Path, *, tenant: SyntheticTenant | None = None) -> Proof:
    """Open every door that is here, writing one chain to `records`.

    A fresh `Engine` per seam, threaded by `last_digest`, rather than one engine shared by all of
    them. Sharing would accumulate the session tally, so the second door would be answering a
    different question from the first and the disagreement would be ours rather than the adapter's.
    Separate engines continuing one chain is what a real deployment does anyway — every `neti hook`
    invocation is its own process.

    The sink is a real file. The records below are the ones those seams actually sealed, not a
    re-run afterwards, and `neti verify -r <records>` reads the same file — which is the difference
    between showing evidence and describing it.
    """
    tenant = tenant or default_tenant()
    policy = load_policy(policy_path).model_copy(update={"mode": Mode.ENFORCE})
    proof = Proof(tool=TOOL, args=dict(ARGS), records_path=records, policy_path=policy_path)

    sink = JsonlSink(records)
    try:
        for seam, needs in NEEDS.items():
            if not _available(needs):
                proof.seams.append(
                    SeamProof(seam=seam, what=WHAT[seam], driven=False, missing=needs)
                )
                continue

            engine = Engine(
                policy=policy,
                resolvers=resolvers_for_client(GraphClient(_CRED, transport=tenant.transport())),
                last_digest=chain_head(records),
                # Every number here comes from the fixture tenant, and the record has to say so.
                synthetic=True,
            )
            verdict, magnitude, sentence = DRIVERS[seam](engine, sink)
            proof.seams.append(
                SeamProof(
                    seam=seam,
                    what=WHAT[seam],
                    driven=True,
                    verdict=verdict,
                    magnitude=magnitude,
                    sentence=sentence,
                )
            )
    finally:
        sink.close()

    written = list(read_records(records))
    proof.records = len(written)
    proof.chain_ok, _bad = verify_chain(written)
    proof.head = written[-1].record_digest if written else None
    return proof


# --------------------------------------------------------------------------- the rendering


def format_proof(proof: Proof) -> str:
    args = ", ".join(f'{k}: "{v}"' for k, v in proof.args.items())
    out: list[str] = [
        "── PROOF ─────────────────────────────  ONE CALL, EVERY DOOR ON THIS MACHINE",
        "",
        f"   {proof.tool}({args})",
        "",
    ]

    width = max(len(s.seam) for s in proof.seams)
    out.append(f"   {'door':<{width}}  {'verdict':<8} {'magnitude':>10}   through")
    for seam in proof.seams:
        if seam.driven:
            magnitude = "—" if seam.magnitude is None else f"{seam.magnitude:,}"
            out.append(
                f"   {seam.seam:<{width}}  {seam.verdict or '—':<8} {magnitude:>10}   {seam.what}"
            )
        else:
            # Never in the verdict/magnitude columns. A row that reads like a measurement for a door
            # nobody opened is the one thing this command exists not to do.
            out.append(
                f"   {seam.seam:<{width}}  {'not here':<8} {'—':>10}   "
                f"needs `{seam.missing}`; proven by {PROVEN_BY}"
            )
    out.append("")

    driven, cited = len(proof.driven), len(proof.cited)
    if driven:
        agreement = (
            "all agreeing on the verdict, the magnitude and the sentence — byte for byte"
            if proof.agreed
            else "AND THEY DISAGREE — see the sentences below"
        )
        out.append(f"   {driven} door(s) opened here,")
        out.append(f"   {agreement}.")
    if cited:
        out.append(
            f"   {cited} more need an SDK this install does not carry. They are not claimed above;"
        )
        out.append(
            f"   {PROVEN_BY} drives all {len(proof.seams)} and asserts the same three things."
        )
    out.append("")

    if proof.driven:
        out.append("   What every one of them handed back to the model:")
        out.append("")
        for line in _wrap(proof.driven[0].sentence, 72):
            out.append(f"     {line}")
        out.append("")

    out.append(f"   {proof.records} decision(s) sealed into one hash chain, and re-checked here:")
    out.append(
        f"     chain {'intact' if proof.chain_ok else 'BROKEN'}   head {(proof.head or '—')[:32]}…"
    )
    if proof.records_path is not None:
        # The full command, including `--mode enforce`. Observe and enforce are different policies
        # with different digests, so the obvious form of this reports "decided under a different
        # policy" and reads as a failure when it is the design working. An instruction that does
        # not work is worse than none in a demo.
        out.append(f"     neti verify -r {proof.records_path} \\")
        out.append(f"       --config {proof.policy_path} --mode enforce")
    out.append("")
    out.append(
        "   The numbers are from the synthetic tenant and every record says so, so this "
        "demonstrates"
    )
    out.append(
        "   behaviour rather than reporting a finding. What it proves is the part that is hard to "
        "take"
    )
    out.append("   on trust: the door your agent uses does not change the answer it gets.")
    return "\n".join(out)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width=width) or [""]
