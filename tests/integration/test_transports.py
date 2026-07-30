"""The two ways an agent actually reaches the gate today.

`test_gateway.py` proves the decision is right. These prove it *arrives* — over the local stdio
transport every MCP client launches its servers with, and through the Claude Code `PreToolUse` hook,
which is the only seam that exists for a harness's own built-in tools.

The stdio tests run a real child process rather than a fake, because everything that makes stdio
hard is real-process behaviour: interleaved server logs, unsolicited notifications, a stdout that is
also the protocol. A fake upstream would pass while the shipped thing corrupted the stream.
"""

from __future__ import annotations

import io
import json
import sys
import textwrap
from typing import Any

import pytest

from neti.adapters.claude_code import normalise_tool, run_hook
from neti.config.policy import Policy, load_policy
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.synthetic import SyntheticTenant, default_tenant
from neti.gateway.mcp import McpGateway
from neti.gateway.stdio import StdioUpstream, serve_stdio
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from tests.integration.test_inventory import EXAMPLE

CRED = ClientCredential(tenant_id="t", client_id="c", client_secret="s")

# A minimal MCP server that does everything awkward a real one does: answers requests out of order
# is not required, but logging to stderr, emitting an unsolicited notification, and echoing tool
# calls are — each of those broke an earlier draft of the transport.
SERVER = textwrap.dedent(
    """
    import json, sys
    print("server: starting up", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if msg.get("method") == "tools/call":
            # Unsolicited server -> client traffic, interleaved with the response.
            print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message",
                              "params": {"level": "info", "data": "working"}}), flush=True)
        if "id" not in msg:
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                          "result": {"echo": msg.get("method"),
                                     "params": msg.get("params")}}), flush=True)
    """
)


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


def build_engine(tenant: SyntheticTenant, mode: Mode) -> Engine:
    policy: Policy = load_policy(EXAMPLE).model_copy(update={"mode": mode})
    client = GraphClient(CRED, transport=tenant.transport())
    return Engine(policy=policy, resolvers=resolvers_for_client(client))


def rpc(tool: str, args: dict[str, Any], call_id: int = 1) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
    )


def run_stdio(engine: Engine, lines: list[str]) -> list[dict[str, Any]]:
    """Drive a real child server through the gate and collect what the client would have seen."""
    upstream = StdioUpstream([sys.executable, "-c", SERVER])
    gateway = McpGateway(engine=engine, upstream=upstream)
    out = io.StringIO()
    try:
        serve_stdio(
            gateway,
            upstream=upstream,
            stdin=io.StringIO("\n".join(lines) + "\n"),
            stdout=out,
        )
    finally:
        upstream.close()
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


# ---------------------------------------------------------------------------- stdio


def test_stdio_forwards_a_call_that_fits(tenant: SyntheticTenant) -> None:
    seen = run_stdio(build_engine(tenant, Mode.ENFORCE), [rpc("send_email", {"to": "g-team"})])
    responses = [m for m in seen if "id" in m]
    assert len(responses) == 1
    assert responses[0]["result"]["echo"] == "tools/call"


def test_stdio_blocks_the_oversized_call_before_the_server_sees_it(
    tenant: SyntheticTenant,
) -> None:
    seen = run_stdio(
        build_engine(tenant, Mode.ENFORCE),
        [rpc("remove_group_members", {"group": "g-eng-all"})],
    )
    responses = [m for m in seen if "id" in m]
    assert len(responses) == 1
    result = responses[0]["result"]
    # A tool result, never a protocol error: the agent must be able to read this and re-plan.
    assert "error" not in responses[0]
    assert result["isError"] is True
    assert "41,203" in result["content"][0]["text"]
    # The child never ran the tool, so it never announced that it was working.
    assert not [m for m in seen if m.get("method") == "notifications/message"]


def test_stdio_forwards_server_initiated_messages(tenant: SyntheticTenant) -> None:
    """A notification the server sent on its own must reach the client verbatim."""
    seen = run_stdio(build_engine(tenant, Mode.ENFORCE), [rpc("send_email", {"to": "g-team"})])
    notes = [m for m in seen if m.get("method") == "notifications/message"]
    assert len(notes) == 1
    assert notes[0]["params"]["data"] == "working"


def test_stdio_passes_non_tool_traffic_through(tenant: SyntheticTenant) -> None:
    """Gating `initialize` or `tools/list` would break negotiation and the session with it."""
    handshake = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {}})
    listing = json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/list"})
    seen = run_stdio(build_engine(tenant, Mode.ENFORCE), [handshake, listing])
    assert sorted(m["id"] for m in seen if "id" in m) == [7, 8]
    assert all("isError" not in m.get("result", {}) for m in seen if "id" in m)


def test_stdio_reports_a_parse_error_as_a_protocol_error(tenant: SyntheticTenant) -> None:
    seen = run_stdio(build_engine(tenant, Mode.ENFORCE), ["{not json"])
    assert seen[0]["error"]["code"] == -32700


def test_stdio_observe_mode_forwards_everything(tenant: SyntheticTenant) -> None:
    seen = run_stdio(
        build_engine(tenant, Mode.OBSERVE),
        [rpc("remove_group_members", {"group": "g-eng-all"})],
    )
    responses = [m for m in seen if "id" in m]
    assert responses[0]["result"]["echo"] == "tools/call"


def test_stdio_refuses_an_empty_command() -> None:
    with pytest.raises(ValueError, match="no command"):
        StdioUpstream([])


# ---------------------------------------------------------------------------- Claude Code hook


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("mcp__entra__remove_group_members", "remove_group_members"),
        ("mcp__my_server__send_email", "send_email"),
        ("remove_group_members", "remove_group_members"),
        ("Bash", "Bash"),
    ],
)
def test_hook_normalises_tool_names(name: str, expected: str) -> None:
    """One policy file governs a tool whichever route it arrives by."""
    assert normalise_tool(name) == expected


def hook_event(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": "s1",
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": args,
    }


def test_hook_denies_the_oversized_call(tenant: SyntheticTenant) -> None:
    engine = build_engine(tenant, Mode.ENFORCE)
    out = run_hook(engine, hook_event("remove_group_members", {"group": "g-eng-all"}))
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "41,203" in decision["permissionDecisionReason"]
    assert decision["neti"]["resolved"] == 41203


def test_hook_asks_when_the_target_cannot_be_sized(tenant: SyntheticTenant) -> None:
    engine = build_engine(tenant, Mode.ENFORCE)
    out = run_hook(engine, hook_event("send_email", {"to": "g-ddg"}))
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_hook_says_nothing_when_the_call_fits(tenant: SyntheticTenant) -> None:
    """A pass must not emit `allow` — that would bypass the operator's own permission rules."""
    engine = build_engine(tenant, Mode.ENFORCE)
    assert run_hook(engine, hook_event("send_email", {"to": "g-team"})) == {}


def test_hook_ignores_events_it_was_not_built_for(tenant: SyntheticTenant) -> None:
    engine = build_engine(tenant, Mode.ENFORCE)
    event = hook_event("remove_group_members", {"group": "g-eng-all"})
    event["hook_event_name"] = "PostToolUse"
    assert run_hook(engine, event) == {}


def test_hook_records_passes_as_well_as_denials(tenant: SyntheticTenant) -> None:
    """Recording only the denials would make every report read as pure friction."""
    engine = build_engine(tenant, Mode.ENFORCE)

    class Sink:
        def __init__(self) -> None:
            self.written: list[Any] = []

        def write(self, record: Any) -> None:
            self.written.append(record)

    sink = Sink()
    run_hook(engine, hook_event("send_email", {"to": "g-team"}), sink)
    run_hook(engine, hook_event("remove_group_members", {"group": "g-eng-all"}), sink)
    assert [str(r.verdict) for r in sink.written] == ["allow", "block"]


def test_hook_denial_reads_the_same_as_the_mcp_denial(tenant: SyntheticTenant) -> None:
    """One denial, one owner. An agent should not be able to tell which transport stopped it."""
    engine = build_engine(tenant, Mode.ENFORCE)
    hooked = run_hook(engine, hook_event("remove_group_members", {"group": "g-eng-all"}))

    gateway = McpGateway(engine=build_engine(tenant, Mode.ENFORCE), upstream=_Silent())
    response = gateway.handle(json.loads(rpc("remove_group_members", {"group": "g-eng-all"})))
    assert response is not None
    assert (
        hooked["hookSpecificOutput"]["permissionDecisionReason"]
        == response["result"]["content"][0]["text"]
    )


class _Silent:
    def send(self, message: dict[str, Any], session_id: str | None) -> dict[str, Any] | None:
        raise AssertionError("a blocked call must never reach the server")
