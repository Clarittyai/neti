"""The HTTP front, over a real socket.

Everything else mocks the transport. This exercises the one path that cannot be mocked away: an MCP
client speaking JSON-RPC over HTTP to a listening server, which is what a customer's config change
actually points at.
"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from neti.config.policy import load_policy
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.synthetic import SyntheticTenant, default_tenant
from neti.gateway.mcp import McpGateway
from neti.gateway.server import SESSION_HEADER, _handler_class
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from tests.integration.test_gateway import FakeUpstream
from tests.integration.test_inventory import EXAMPLE

CRED = ClientCredential(tenant_id="t", client_id="c", client_secret="s")


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


class RunningGate:
    def __init__(self, gateway: McpGateway) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(gateway))
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def start(tenant: SyntheticTenant, mode: Mode) -> tuple[RunningGate, FakeUpstream, GraphClient]:
    policy = load_policy(EXAMPLE).model_copy(update={"mode": mode})
    client = GraphClient(CRED, transport=tenant.transport())
    upstream = FakeUpstream()
    gateway = McpGateway(
        engine=Engine(policy=policy, resolvers=resolvers_for_client(client)), upstream=upstream
    )
    return RunningGate(gateway), upstream, client


def rpc(url: str, tool: str, args: dict[str, Any], session: str | None = None) -> dict[str, Any]:
    headers = {SESSION_HEADER: session} if session else {}
    response = httpx.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        },
        headers=headers,
        timeout=10,
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def test_a_blocked_call_returns_http_200_with_an_error_result(tenant: SyntheticTenant) -> None:
    """HTTP 200 is correct here and the distinction is load-bearing.

    The *transport* succeeded; the *tool* was refused. An MCP client that saw a 4xx would treat the
    session as broken rather than handing the model something it can act on.
    """
    gate, upstream, client = start(tenant, Mode.ENFORCE)
    try:
        body = rpc(gate.url, "remove_group_members", {"group": "g-eng-all"})
    finally:
        gate.close()
        client.close()

    assert upstream.tools_called == []
    assert body["result"]["isError"] is True
    assert body["result"]["_meta"]["neti"]["resolved"] == 41_203


def test_an_allowed_call_reaches_the_server(tenant: SyntheticTenant) -> None:
    gate, upstream, client = start(tenant, Mode.ENFORCE)
    try:
        body = rpc(gate.url, "remove_group_members", {"group": "g-solo"})
    finally:
        gate.close()
        client.close()
    assert upstream.tools_called == ["remove_group_members"]
    assert "isError" not in body["result"]


def test_observe_mode_lets_everything_through_over_the_wire(tenant: SyntheticTenant) -> None:
    gate, upstream, client = start(tenant, Mode.OBSERVE)
    try:
        body = rpc(gate.url, "remove_group_members", {"group": "g-eng-all"})
    finally:
        gate.close()
        client.close()
    assert upstream.tools_called == ["remove_group_members"]
    assert "isError" not in body["result"]


def test_session_header_is_read_from_the_request(tenant: SyntheticTenant) -> None:
    gate, upstream, client = start(tenant, Mode.OBSERVE)
    try:
        rpc(gate.url, "send_email", {"to": "g-solo"}, session="sess-a")
    finally:
        gate.close()
        client.close()
    assert upstream.sessions == ["sess-a"]


def test_malformed_json_is_a_protocol_error_not_a_verdict(tenant: SyntheticTenant) -> None:
    """A parse failure genuinely is a protocol error — unlike a denial, which is not."""
    gate, _, client = start(tenant, Mode.ENFORCE)
    try:
        response = httpx.post(gate.url, content=b"{not json", timeout=10)
    finally:
        gate.close()
        client.close()
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700


def test_a_notification_is_accepted_with_no_body(tenant: SyntheticTenant) -> None:
    gate, upstream, client = start(tenant, Mode.ENFORCE)
    try:
        response = httpx.post(
            gate.url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=10
        )
    finally:
        gate.close()
        client.close()
    assert response.status_code == 202
    assert response.content == b""
    assert len(upstream.sent) == 1


def test_a_gate_crash_is_reported_as_an_internal_error_not_a_denial() -> None:
    """An operator debugging a stopped call must be able to tell "the gate decided" from
    "the gate broke". Reporting a crash as a verdict would make that impossible."""

    class Exploding:
        def handle(self, message: dict[str, Any], session_id: str | None) -> dict[str, Any]:
            raise RuntimeError("resolver registry is on fire")

    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(Exploding()))  # type: ignore[arg-type]
    url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = httpx.post(
            url,
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "x", "arguments": {}},
            },
            timeout=10,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert response.status_code == 500
    body = json.loads(response.content)
    assert body["error"]["code"] == -32603
    assert "on fire" in body["error"]["message"]
    assert "result" not in body, "a crash must never look like a verdict"
