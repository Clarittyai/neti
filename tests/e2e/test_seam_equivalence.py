"""One policy, one call, seven seams — and they must agree exactly.

`neti` sits in front of an agent through whichever door that agent already has: MCP over stdio or
HTTP, Claude Code's `PreToolUse` hook, the Anthropic tool runner, the OpenAI Agents SDK, LangChain
and LangGraph, or a loop somebody wrote themselves. Each of those has its own adapter, its own
argument shape, and its own idea of what "refuse" looks like — a JSON-RPC result, a hook decision, a
`ToolMessage`, a guardrail rejection, a returned string.

**A verdict that depends on which door the call came through is a bug in the product, not in the
adapter.** The codebase already believed this — `test_hook_denial_reads_the_same_as_the_mcp_denial`
and `test_the_denial_is_word_for_word_the_hook_denial` compare two seams each — but those were
written before the three SDK adapters existed, so three of the seven sat outside any such check.
This is those tests generalised into one table: every seam is a row, every scenario is a column, and
adding a seam means adding a row rather than remembering to write a comparison.

What is asserted is deliberately strict: the same **verdict**, the same **magnitude**, and the same
**sentence**, byte for byte. Wording is included because the sentence is the product's actual
output — it is what the model reads and what makes it retry with a narrower target — and a seam that
quietly rephrases it has changed the thing the agent acts on.
"""

from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from neti.config.policy import Policy, load_policy
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.synthetic import SyntheticTenant, default_tenant
from neti.gateway.mcp import McpGateway
from neti.gateway.stdio import StdioUpstream, serve_stdio
from neti.preflight import Preflight
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from tests.integration.test_inventory import EXAMPLE

CRED = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")

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


# `examples/entra.yaml` under enforce. The magnitudes come from the synthetic tenant, so every seam
# resolves the identical numbers and any difference is the seam's own.
CASES = [
    Case("blocked", "remove_group_members", {"group": "g-eng-all"}, "block"),
    Case("allowed", "send_email", {"to": "g-team"}, "allow"),
    Case("unsizeable", "remove_group_members", {"group": "not-a-group-at-all"}, "block"),
    Case("ungated", "read_documentation", {"topic": "anything"}, "allow"),
    # The same tool arriving with an MCP server prefix, which is how Claude Code names tools from
    # an MCP server. It must hit the same policy entry rather than falling through as unknown.
    Case("mcp-prefixed", "mcp__entra__remove_group_members", {"group": "g-eng-all"}, "block"),
]


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


def build_engine(tenant: SyntheticTenant) -> Engine:
    policy: Policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.ENFORCE})
    return Engine(
        policy=policy,
        resolvers=resolvers_for_client(GraphClient(CRED, transport=tenant.transport())),
    )


def build_preflight(tenant: SyntheticTenant, tmp_path: Path, approver: Any = None) -> Preflight:
    """The in-process gate, optionally with a control plane behind it.

    The three SDK adapters reach approvals only through here, so an approver that did not arrive
    would make `CONFIRM` a flat denial on those runtimes while the other four asked a human — the
    paid tier silently worth less depending on which framework somebody chose.
    """
    pf = Preflight.demo(EXAMPLE, mode="enforce", records=tmp_path / "records.ndjson")
    if approver is None:
        return pf
    return Preflight(engine=pf.engine, sink=pf.sink, approver=approver)


# ---------------------------------------------------------------------------- the seam drivers
#
# Each takes a case and returns an Outcome. Everything runtime-specific lives here; the test below
# knows nothing about JSON-RPC, hook protocols or guardrail objects.


def via_preflight(
    tenant: SyntheticTenant, tmp_path: Path, case: Case, approver: Any = None
) -> Outcome:
    verdict = build_preflight(tenant, tmp_path, approver).check(case.tool, case.args)
    # `proceeds`, not `verdict`. Every other seam reports what *happened* — the tool ran or it did
    # not — and `Verdict.verdict` is the decision that was reached before a human answered. A
    # granted approval leaves it reading `confirm` while the call goes through, so comparing that
    # field would make this seam look like the odd one out when it is behaving identically.
    return Outcome(
        verdict="allow" if verdict.proceeds else verdict.verdict,
        magnitude=verdict.payload.get("resolved"),
        sentence=verdict.message,
    )


def via_hook(tenant: SyntheticTenant, tmp_path: Path, case: Case, approver: Any = None) -> Outcome:
    from neti.adapters.claude_code import run_hook

    out = run_hook(
        build_engine(tenant),
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


def via_mcp_http(
    tenant: SyntheticTenant, tmp_path: Path, case: Case, approver: Any = None
) -> Outcome:
    class Upstream:
        def send(self, message: dict[str, Any], session_id: str | None) -> dict[str, Any] | None:
            return {"jsonrpc": "2.0", "id": message["id"], "result": {"content": []}}

    gateway = McpGateway(engine=build_engine(tenant), upstream=Upstream(), approver=approver)
    response = gateway.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": case.tool, "arguments": case.args},
        }
    )
    return _from_mcp(response)


def via_mcp_stdio(
    tenant: SyntheticTenant, tmp_path: Path, case: Case, approver: Any = None
) -> Outcome:
    """A real child process over a real pipe, not an in-process fake."""
    upstream = StdioUpstream([sys.executable, "-c", SERVER])
    gateway = McpGateway(engine=build_engine(tenant), upstream=upstream, approver=approver)
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


def via_anthropic(
    tenant: SyntheticTenant, tmp_path: Path, case: Case, approver: Any = None
) -> Outcome:
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

    gated = gate_tool(build_preflight(tenant, tmp_path, approver), make(case.tool))
    result = gated.call(case.args)
    if ran:
        return Outcome("allow", None, "")
    return Outcome(_verdict_of(str(result)), _magnitude_of(str(result)), str(result))


def via_openai_agents(
    tenant: SyntheticTenant, tmp_path: Path, case: Case, approver: Any = None
) -> Outcome:
    from agents import Agent
    from agents.tool_context import ToolContext
    from agents.tool_guardrails import ToolInputGuardrailData

    from neti.adapters.openai_agents import verdict_for

    data = ToolInputGuardrailData(
        context=ToolContext(
            context=None,
            tool_name=case.tool,
            tool_call_id="call_1",
            tool_arguments=json.dumps(case.args),
        ),
        agent=Agent(name="test"),
    )
    out = verdict_for(build_preflight(tenant, tmp_path, approver), data)
    if out.behavior["type"] == "allow":
        return Outcome("allow", None, "")
    payload = out.output_info["neti"]
    return Outcome(payload["verdict"], payload.get("resolved"), out.behavior["message"])


def via_langchain(
    tenant: SyntheticTenant, tmp_path: Path, case: Case, approver: Any = None
) -> Outcome:
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
    gated = gate_tool(build_preflight(tenant, tmp_path, approver), inner).model_copy(
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


SEAMS = {
    "preflight": via_preflight,
    "hook": via_hook,
    "mcp-http": via_mcp_http,
    "mcp-stdio": via_mcp_stdio,
    "anthropic": via_anthropic,
    "openai-agents": via_openai_agents,
    "langchain": via_langchain,
}

NEEDS_SDK = {"anthropic": "anthropic", "openai-agents": "agents", "langchain": "langchain_core"}


def outcome(
    seam: str, tenant: SyntheticTenant, tmp_path: Path, case: Case, approver: Any = None
) -> Outcome:
    module = NEEDS_SDK.get(seam)
    if module:
        pytest.importorskip(module, reason=f"{seam} needs the sdks extra")
    return SEAMS[seam](tenant, tmp_path, case, approver)


# ---------------------------------------------------------------------------- the invariant


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
@pytest.mark.parametrize("seam", sorted(SEAMS))
def test_every_seam_reaches_the_same_verdict(
    seam: str, case: Case, tenant: SyntheticTenant, tmp_path: Path
) -> None:
    """The door a call came through must not change whether it is allowed."""
    assert outcome(seam, tenant, tmp_path, case).verdict == case.verdict


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_every_seam_agrees_on_the_magnitude_and_the_sentence(
    case: Case, tenant: SyntheticTenant, tmp_path: Path
) -> None:
    """One denial, one owner.

    The sentence is compared byte for byte because it *is* the product's output — it is what the
    model reads and what makes it retry with a narrower target. A seam that rephrases it has changed
    the thing the agent acts on, which no per-adapter test would notice.
    """
    results = {seam: outcome(seam, tenant, tmp_path, case) for seam in sorted(SEAMS)}

    magnitudes = {seam: out.magnitude for seam, out in results.items() if out.magnitude is not None}
    assert len(set(magnitudes.values())) <= 1, f"seams disagree on the magnitude: {magnitudes}"

    sentences = {seam: out.sentence for seam, out in results.items() if out.sentence}
    assert len(set(sentences.values())) <= 1, "seams disagree on the denial wording:\n" + "\n".join(
        f"  {seam}: {text!r}" for seam, text in sorted(sentences.items())
    )


def test_a_blocked_call_names_its_number_everywhere_it_can(
    tenant: SyntheticTenant, tmp_path: Path
) -> None:
    """The specific claim the product makes: the model is told *how big*, not merely "no".

    Asserted against the sentence rather than a payload, because a number in a structured field the
    model never reads does not make it narrow its scope.
    """
    blocked = CASES[0]
    for seam in sorted(SEAMS):
        out = outcome(seam, tenant, tmp_path, blocked)
        assert "41,203" in out.sentence, f"{seam} blocked without naming the magnitude"


def test_an_allowed_call_says_nothing_at_all(tenant: SyntheticTenant, tmp_path: Path) -> None:
    """Silence on the happy path is a contract, not an accident: the hook emitting anything at all
    would override the permission rules an operator already configured."""
    allowed = next(c for c in CASES if c.name == "allowed")
    for seam in sorted(SEAMS):
        assert outcome(seam, tenant, tmp_path, allowed).sentence == ""


def test_the_seam_table_covers_every_shipped_adapter() -> None:
    """The table is only an invariant while it is complete.

    A new adapter added to `neti.adapters` without a row here would be the same hole the three SDK
    adapters sat in: individually tested, never compared, free to drift.
    """
    import pkgutil

    import neti.adapters

    shipped = {name for _, name, _ in pkgutil.iter_modules(neti.adapters.__path__)}
    covered = {"claude_code", "anthropic_tools", "openai_agents", "langchain_tools"}
    assert shipped == covered, (
        f"adapters with no seam-equivalence row: {sorted(shipped - covered)}. "
        "Add a driver above so it cannot drift from the others."
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
    seam: str, tenant: SyntheticTenant, tmp_path: Path
) -> None:
    """The free tier, and the behaviour every paid install degrades to.

    With no control plane a `CONFIRM` means "this does not proceed without a human" and there is no
    human, so it stops. That has to be true on all seven seams or the tier boundary is not a
    boundary — an install would be quietly more permissive on whichever runtime forgot.
    """
    assert outcome(seam, tenant, tmp_path, NEEDS_A_HUMAN).verdict == "confirm"


@pytest.mark.parametrize("seam", sorted(SEAMS))
def test_a_granted_approval_lets_the_call_through_on_every_seam(
    seam: str, tenant: SyntheticTenant, tmp_path: Path
) -> None:
    """`test_a_grant_is_honoured_identically_on_all_three_seams` extended to seven.

    That test predates the Anthropic, OpenAI Agents and LangChain adapters, and those reach the
    control plane by a different route — through `Preflight` rather than through `Gatekeeper`
    directly. So the runtimes most people use were the ones with no assertion that approvals work
    on them at all: a paying customer on LangChain could have been getting flat denials where a
    customer on MCP got a human, and nothing would have said so.
    """
    assert outcome(seam, tenant, tmp_path, NEEDS_A_HUMAN, approver("granted")).verdict == "allow"


@pytest.mark.parametrize("seam", sorted(SEAMS))
def test_a_denied_approval_stops_the_call_on_every_seam(
    seam: str, tenant: SyntheticTenant, tmp_path: Path
) -> None:
    """The direction that matters if the plumbing is wrong.

    A grant arriving where it should not is the failure a reviewer will ask about, so the refusal
    path is asserted separately rather than inferred from the grant working.
    """
    assert outcome(seam, tenant, tmp_path, NEEDS_A_HUMAN, approver("denied")).verdict != "allow"


@pytest.mark.parametrize("seam", sorted(SEAMS))
def test_an_approver_can_never_make_a_block_proceed_on_any_seam(
    seam: str, tenant: SyntheticTenant, tmp_path: Path
) -> None:
    """The tier boundary's load-bearing claim: a control plane can only ever be *more* permissive
    about a `CONFIRM`, and a `BLOCK` is never put to a human at all.

    If any seam escalated a block, paying for approvals would buy a way around a declared ceiling —
    which is the opposite of what the product is.
    """
    blocked = next(c for c in CASES if c.name == "blocked")
    assert outcome(seam, tenant, tmp_path, blocked, approver("granted")).verdict == "block"
