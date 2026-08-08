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

import contextlib
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from neti.config.policy import Policy, load_policy
from neti.core.record import verify_chain
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.synthetic import SyntheticTenant, default_tenant
from neti.gateway.mcp import McpGateway
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from neti.store.jsonl import JsonlSink, chain_head, read_records

__all__ = ["Call", "Proof", "SeamProof", "format_proof", "pick_call", "run_proof"]

_CRED = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")

# The call every door is asked about, when the policy is the shipped Entra example. Chosen because
# its answer is unmistakable: 41,203 people is not a number anybody squints at, and every seam has
# to produce that same number or fail here.
TOOL = "remove_group_members"
ARGS: dict[str, Any] = {"group": "g-eng-all"}

# Outside any project root, present on every machine this runs on, and one file — so the magnitude
# is not the point and cannot be mistaken for it. Never opened; the resolver counts paths.
OUTSIDE = "/etc/hosts"


@dataclass(frozen=True)
class Call:
    """The one call this run drives, and where its numbers come from.

    `prove` used to hard-code the Entra fixture call, which meant the command **could not run on
    the policy `neti start` writes** — the default config was the one config it refused. It printed
    a tidy error naming `neti prove` as the thing to run instead, which was the command that had
    just failed. Meanwhile `neti start`, the demo transcripts and the README all point at it.

    So the call is chosen from the policy in hand. What `prove` asks is whether every door agrees,
    and any call the policy stops answers that.
    """

    tool: str
    args: dict[str, Any]
    synthetic: bool
    """True when the magnitudes come from the built-in tenant rather than this disk. It goes into
    the record and inside the digest, so a fixture number can never be read back as a measurement —
    which is exactly why it is a property of the *call* and not a constant of this module."""

    why: str
    """One line for the banner: what makes the policy stop this, so a reader is not left inferring
    it from a verdict."""


def pick_call(policy: Policy) -> Call | None:
    """A call this policy will stop, or `None` if nothing here can be driven.

    Every seam driver asserts the call did not reach the tool, so a call that merely flags proves
    nothing about agreement — half the doors would report a pass-through and be right. The order
    below is the order of certainty, not of severity.
    """
    if TOOL in policy.tools:
        return Call(
            TOOL, dict(ARGS), synthetic=True, why="41,203 principals, from the fixture tenant"
        )

    gated = [
        (tool, pointer, spec)
        for tool in sorted(policy.tools)
        for pointer, spec in policy.gate_specs(tool).items()
        if spec.resolver == "fs.paths"
    ]
    if not gated:
        return None

    tool, pointer, _spec = gated[0]
    key = pointer.lstrip("/")

    if policy.outside_root is not None:
        return Call(
            tool,
            {key: OUTSIDE},
            synthetic=False,
            why=f"{OUTSIDE} is outside the declared root",
        )

    for rule in policy.sensitive:
        # An example the rule really matches, built from the rule rather than guessed: `**/.env*`
        # becomes `.env`. A pattern we cannot turn into a path is skipped rather than approximated.
        literal = rule.match.replace("**/", "").replace("*", "")
        if literal and "/" not in literal.strip("/"):
            return Call(
                tool,
                {key: literal},
                synthetic=False,
                why=f"{literal} matches the off-limits rule {rule.match}",
            )
    return None


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
    "tool-loop": None,
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
    "llamaindex": "llama_index.core",
    "smolagents": "smolagents",
    "semantic-kernel": "semantic_kernel",
}

WHAT: dict[str, str] = {
    "preflight": "Preflight.check, called directly",
    "tool-loop": "a hand-written Anthropic or OpenAI tool loop",
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
    "llamaindex": "LlamaIndex",
    "smolagents": "smolagents",
    "semantic-kernel": "Semantic Kernel",
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
    why: str = ""
    """What makes the policy stop this call — printed, so the reader is not inferring it."""

    synthetic: bool = True
    """Whether the magnitudes came from the fixture tenant. Drives what the closing paragraph
    claims, which has to agree with the marker sealed into every record below it."""

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
        """Every door that was opened returned the same verdict and the same sentence, and no two
        of them returned different numbers.

        Byte-for-byte on the sentence, because the sentence is what the model reads and what makes
        it retry with a narrower target. A seam that rephrased it has changed the thing the agent
        acts on, and that is not agreement.

        **A door that reported no number has not contradicted one that did.** Half these seams hand
        back only text, so `_classify` recovers the magnitude by reading `resolves to N` out of the
        sentence — which works for a ceiling breach and finds nothing in a sentence that has no
        number in it, like the one the location rule produces. Comparing `None` against `1` there
        printed AND THEY DISAGREE over fifteen doors that had all said exactly the same thing.
        That is a false alarm on the product's own honesty check, which is the one place it cannot
        afford one. Two doors reporting *different* numbers is still a disagreement.
        """
        if len({(s.verdict, s.sentence) for s in self.driven}) != 1:
            return False
        return len({s.magnitude for s in self.driven if s.magnitude is not None}) <= 1


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


def _preflight(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    from neti.preflight import Preflight

    verdict = Preflight(engine=engine, sink=sink).check(call.tool, dict(call.args))
    return verdict.verdict, verdict.payload.get("resolved"), verdict.message


def _tool_loop(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    from neti.adapters.tool_loop import gate_tools
    from neti.preflight import Preflight

    def ran(**kwargs: Any) -> str:
        raise AssertionError("the gate let the call through")

    tools = gate_tools(Preflight(engine=engine, sink=sink), {call.tool: ran})
    return _classify(str(tools[call.tool](**call.args)))


def _hook(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    from neti.adapters.claude_code import run_hook

    event = {"hook_event_name": "PreToolUse", "tool_name": call.tool, "tool_input": call.args}
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


def _mcp(engine: Engine, upstream: Any, sink: Any, call: Call) -> tuple[str, int | None, str]:
    gateway = McpGateway(engine=engine, upstream=upstream, sink=sink)
    response = gateway.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": call.tool, "arguments": call.args},
        }
    )
    assert response is not None
    result = response["result"]
    payload = result.get("_meta", {}).get("neti", {})
    return payload.get("verdict", "block"), payload.get("resolved"), result["content"][0]["text"]


def _mcp_http(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    return _mcp(engine, _Upstream(), sink, call)


def _mcp_stdio(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    """A real child process on a real pipe, spawned here and killed here."""
    from neti.gateway.stdio import StdioUpstream

    upstream = StdioUpstream([sys.executable, "-c", _CHILD])
    try:
        return _mcp(engine, upstream, sink, call)
    finally:
        upstream.close()


def _via_preflight_adapter(engine: Engine, sink: Any, drive: Any) -> tuple[str, int | None, str]:
    """The SDK adapters all gate through `Preflight`, so they share one shape."""
    from neti.preflight import Preflight

    driven: tuple[str, int | None, str] = drive(Preflight(engine=engine, sink=sink))
    return driven


def _anthropic(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    from anthropic.lib.tools import beta_tool

    from neti.adapters.anthropic_tools import gate_tool

    @beta_tool
    def tool(**kwargs: Any) -> str:
        """Do the thing."""
        raise AssertionError("the gate let the call through")

    tool.name = call.tool

    def drive(pf: Any) -> tuple[str, int | None, str]:
        text = str(gate_tool(pf, tool).call(dict(call.args)))
        return _classify(text)

    return _via_preflight_adapter(engine, sink, drive)


def _openai_agents(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    import json as _json

    from agents import Agent
    from agents.tool_context import ToolContext
    from agents.tool_guardrails import ToolInputGuardrailData

    from neti.adapters.openai_agents import verdict_for

    def drive(pf: Any) -> tuple[str, int | None, str]:
        data = ToolInputGuardrailData(
            context=ToolContext(
                context=None,
                tool_name=call.tool,
                tool_call_id="call_1",
                tool_arguments=_json.dumps(call.args),
            ),
            agent=Agent(name="proof"),
        )
        out = verdict_for(pf, data)
        payload = out.output_info["neti"]
        return payload["verdict"], payload.get("resolved"), out.behavior["message"]

    return _via_preflight_adapter(engine, sink, drive)


def _langchain(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    from langchain_core.tools import StructuredTool

    from neti.adapters.langchain_tools import gate_tool

    def run(**kwargs: Any) -> str:
        raise AssertionError("the gate let the call through")

    inner = StructuredTool.from_function(func=run, name=call.tool, description="Do the thing.")

    def drive(pf: Any) -> tuple[str, int | None, str]:
        return _classify(str(gate_tool(pf, inner).invoke(dict(call.args))))

    return _via_preflight_adapter(engine, sink, drive)


def _crewai(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    """The wrapped tool, not the hook pair.

    This used to drive `before_tool_call` and then run the after-hooks over CrewAI's fixed string,
    which is a control flow CrewAI does not have: it returns the instant a before-hook blocks, so
    the after-hook never runs and the model is told "blocked by hook" with no number in it. The
    gate is a wrapped `BaseTool` now, and this drives that.
    """
    from crewai.tools import BaseTool

    from neti.adapters.crewai_hooks import gate_tool
    from neti.preflight import Preflight

    class Inner(BaseTool):
        name: str = call.tool
        description: str = "Do the thing."

        def _run(self, **kwargs: Any) -> str:
            raise AssertionError("the gate let the call through")

    gated = gate_tool(Preflight(engine=engine, sink=sink), Inner())
    return _classify(str(gated.run(**dict(call.args))))


def _pydantic_ai(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
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
                call=ToolCallPart(tool_name=call.tool, args=dict(call.args), tool_call_id="c1"),
                tool_def=ToolDefinition(name=call.tool),
                args=dict(call.args),
            )
        except ToolFailed as failed:
            return _classify(str(failed.message))
        raise AssertionError("the gate let the call through")

    return asyncio.run(go())


def _autogen(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    import asyncio

    from autogen_core.tools import StaticWorkbench

    from neti.adapters.autogen_tools import gate_workbench
    from neti.preflight import Preflight

    bench = gate_workbench(Preflight(engine=engine, sink=sink), StaticWorkbench(tools=[]))
    result = asyncio.run(bench.call_tool(call.tool, dict(call.args)))
    return _classify(str(result.result[0].content))


def _google_adk(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    import asyncio
    from types import SimpleNamespace

    from neti.adapters.google_adk import neti_plugin
    from neti.preflight import Preflight

    plugin = neti_plugin(Preflight(engine=engine, sink=sink))
    out = asyncio.run(
        plugin.before_tool_callback(
            tool=SimpleNamespace(name=call.tool), tool_args=dict(call.args), tool_context=None
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


def _llamaindex(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    import asyncio

    from llama_index.core.tools import FunctionTool

    from neti.adapters.llamaindex_tools import gate_tool
    from neti.preflight import Preflight

    def run(**kwargs: Any) -> str:
        raise AssertionError("the gate let the call through")

    inner = FunctionTool.from_defaults(fn=run, name=call.tool, description="Do the thing.")
    gated = gate_tool(Preflight(engine=engine, sink=sink), inner)
    return _classify(str(asyncio.run(gated.acall(**dict(call.args))).content))


def _smolagents(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    from smolagents import Tool as SmolTool

    from neti.adapters.smolagents_tools import gate_tool
    from neti.preflight import Preflight

    def forward(self: Any, group: str = "") -> str:
        raise AssertionError("the gate let the call through")

    inner = type(
        "Inner",
        (SmolTool,),
        {
            "name": call.tool,
            "description": "Do the thing.",
            "inputs": {
                "group": {"type": "string", "description": "a group", "nullable": True},
            },
            "output_type": "string",
            "forward": forward,
        },
    )()
    gated = gate_tool(Preflight(engine=engine, sink=sink), inner)
    return _classify(str(gated(**dict(call.args))))


def _semantic_kernel(engine: Engine, sink: Any, call: Call) -> tuple[str, int | None, str]:
    import asyncio

    from semantic_kernel import Kernel
    from semantic_kernel.filters import FilterTypes
    from semantic_kernel.functions import kernel_function

    from neti.adapters.semantic_kernel_filters import neti_filter
    from neti.preflight import Preflight

    class Plugin:
        @kernel_function(name=call.tool, description="Do the thing.")
        def run(self, group: str = "") -> str:
            raise AssertionError("the gate let the call through")

    kernel = Kernel()
    kernel.add_plugin(Plugin(), plugin_name="p")
    kernel.add_filter(
        FilterTypes.FUNCTION_INVOCATION, neti_filter(Preflight(engine=engine, sink=sink))
    )
    result = asyncio.run(kernel.invoke(plugin_name="p", function_name=call.tool, **dict(call.args)))
    return _classify(str(result))


DRIVERS: dict[str, Any] = {
    "preflight": _preflight,
    "tool-loop": _tool_loop,
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
    "llamaindex": _llamaindex,
    "smolagents": _smolagents,
    "semantic-kernel": _semantic_kernel,
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
    call = pick_call(policy)
    if call is None:
        raise ValueError(f"{policy_path} gates nothing `neti prove` can drive")
    proof = Proof(
        tool=call.tool,
        args=dict(call.args),
        records_path=records,
        policy_path=policy_path,
        why=call.why,
        synthetic=call.synthetic,
    )

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
                # `providers:` passed through, which it was not before: without the declared root
                # the filesystem resolvers decline, so a coding-agent policy resolved nothing and
                # every door agreed on the wrong answer.
                resolvers=resolvers_for_client(
                    GraphClient(_CRED, transport=tenant.transport()), policy.providers
                ),
                last_digest=chain_head(records),
                # Only when the numbers really came from the fixture. Sealing a measurement as
                # synthetic is the same lie as the reverse, and this marker is inside the digest.
                synthetic=call.synthetic,
            )
            # SDK chatter captured, not silenced by luck. CrewAI prints `Using Tool: <name>` to
            # stdout when a tool is invoked, which landed above the banner — and above the JSON
            # under `--json`, so `neti prove --json | jq` failed on the first line. Whatever a
            # future SDK decides to print, the machine-readable output stays machine-readable.
            noise = io.StringIO()
            with contextlib.redirect_stdout(noise):
                verdict, magnitude, sentence = DRIVERS[seam](engine, sink, call)
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
    ]
    if proof.why:
        # Why this call and not another. Without it a reader has to infer the rule from a verdict,
        # and the call is now chosen from their policy rather than fixed, so it is not guessable.
        out.append(f"   stopped because {proof.why}")
    out.append("")

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
        # The claim names what was actually compared. Half these seams carry only text, so when
        # the sentence holds no number there is no magnitude to agree on — and saying they agreed
        # on one would be the overclaim this command exists to avoid.
        numbers = {s.magnitude for s in proof.driven if s.magnitude is not None}
        agreement = (
            (
                "all agreeing on the verdict, the magnitude and the sentence — byte for byte"
                if numbers
                else "all agreeing on the verdict and the sentence — byte for byte"
            )
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
    if proof.synthetic:
        out.append(
            "   The numbers are from the synthetic tenant and every record says so, so this "
            "demonstrates"
        )
        out.append(
            "   behaviour rather than reporting a finding. What it proves is the part that is "
            "hard to take"
        )
    else:
        # The claim has to follow the call. Saying "from the synthetic tenant" over magnitudes
        # measured on this disk would be false in the direction that matters — the marker inside
        # the digest says otherwise, and a banner contradicting the record is worse than no banner.
        out.append(
            "   Measured on this machine against your own policy, and every record says so."
        )
        out.append("   What it proves is the part that is hard to take on trust:")
        out.append("   the door your agent uses does not change the answer it gets.")
        return "\n".join(out)
    out.append("   on trust: the door your agent uses does not change the answer it gets.")
    return "\n".join(out)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width=width) or [""]
