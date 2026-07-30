"""The MCP gateway.

The protocol behaviours here are the ones that decide whether this is installable. In order:
a denial must be a tool result rather than a protocol error, or a security decision becomes an
outage; non-`tools/call` traffic must pass through untouched, or session negotiation breaks; and
observe mode must never withhold a call, or the install stops being reversible.
"""

from __future__ import annotations

from typing import Any

import pytest

from neti.config.policy import Policy, load_policy
from neti.core.verdict import Mode, Verdict
from neti.engine import Engine
from neti.eval.synthetic import Group, SyntheticTenant, default_tenant
from neti.gateway.mcp import McpGateway
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from neti.store.jsonl import JsonlSink, read_records
from tests.integration.test_inventory import EXAMPLE

CRED = ClientCredential(tenant_id="t", client_id="c", client_secret="s")


class FakeUpstream:
    """Records what actually reached the MCP server. The gate's job is to change this list."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.sessions: list[str | None] = []

    def send(self, message: dict[str, Any], session_id: str | None) -> dict[str, Any] | None:
        self.sent.append(message)
        self.sessions.append(session_id)
        if "id" not in message:
            return None  # a notification
        return {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {"content": [{"type": "text", "text": "done"}]},
        }

    @property
    def tools_called(self) -> list[str]:
        return [m["params"]["name"] for m in self.sent if m.get("method") == "tools/call"]


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


def build(tenant: SyntheticTenant, mode: Mode, **kw: Any) -> tuple[McpGateway, FakeUpstream, Any]:
    policy: Policy = load_policy(EXAMPLE).model_copy(update={"mode": mode})
    client = GraphClient(CRED, transport=tenant.transport())
    engine = Engine(policy=policy, resolvers=resolvers_for_client(client))
    upstream = FakeUpstream()
    return McpGateway(engine=engine, upstream=upstream, **kw), upstream, client


def call(tool: str, args: dict[str, Any], call_id: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }


# --------------------------------------------------------------- enforcement


def test_an_over_ceiling_call_never_reaches_the_server(tenant: SyntheticTenant) -> None:
    gate, upstream, client = build(tenant, Mode.ENFORCE)
    try:
        response = gate.handle(call("remove_group_members", {"group": "g-eng-all"}))
    finally:
        client.close()

    assert upstream.tools_called == [], "the tool ran despite being blocked"
    assert response is not None
    assert response["id"] == 1
    assert response["result"]["isError"] is True


def test_a_denial_is_a_tool_result_not_a_protocol_error(tenant: SyntheticTenant) -> None:
    """The single most important behaviour in the gateway.

    A JSON-RPC `error` kills the agent's run; an `isError` result is something the model reads and
    can act on. Getting this wrong turns every block into an outage.
    """
    gate, _, client = build(tenant, Mode.ENFORCE)
    try:
        response = gate.handle(call("remove_group_members", {"group": "g-eng-all"}))
    finally:
        client.close()

    assert response is not None
    assert "error" not in response, "a denial must not be a JSON-RPC protocol error"
    assert response["result"]["isError"] is True
    text = response["result"]["content"][0]["text"]
    # It must name the number and the ceiling, or the agent cannot learn to narrow its scope.
    assert "41,203" in text
    assert "200" in text
    assert response["result"]["_meta"]["neti"]["resolved"] == 41_203


def test_an_under_ceiling_call_is_forwarded_unchanged(tenant: SyntheticTenant) -> None:
    gate, upstream, client = build(tenant, Mode.ENFORCE)
    try:
        message = call("remove_group_members", {"group": "g-solo"})
        response = gate.handle(message)
    finally:
        client.close()

    assert upstream.sent == [message], "the gate must not rewrite a permitted call"
    assert response is not None
    assert "isError" not in response["result"]


def test_confirm_also_stops_the_call(tenant: SyntheticTenant) -> None:
    """CONFIRM withholds the call too — it is not a softer ALLOW.

    40 members sits between the example policy's confirm band (25) and its block band (200), and
    below the 50-principal cumulative session budget, so the per-call verdict is what is under test.
    """
    tenant.add(Group("g-mid", "Mid-sized", transitive_members=40, app_assignments=0))
    gate, upstream, client = build(tenant, Mode.ENFORCE)
    try:
        response = gate.handle(call("remove_group_members", {"group": "g-mid"}))
    finally:
        client.close()
    assert upstream.tools_called == []
    assert response is not None
    text = response["result"]["content"][0]["text"]
    assert "needs confirmation" in text
    assert "operator must approve" in text


def test_a_session_budget_denial_names_the_budget_not_the_per_call_ceiling(
    tenant: SyntheticTenant,
) -> None:
    """Regression: the denial quoted the per-argument ceiling even when the session budget fired,
    telling the agent to narrow a call that was already small enough.

    Three 25-member removals. Each passes its per-call ceiling (`above: 25` is exclusive), so each
    proceeds and consumes budget. The third pushes the session total to 75, over the declared
    50-principal cumulative ceiling — and only the budget has anything to complain about.
    """
    tenant.add(Group("g-25", "Twenty five", transitive_members=25, app_assignments=0))
    gate, upstream, client = build(tenant, Mode.ENFORCE)
    try:
        first = gate.handle(call("remove_group_members", {"group": "g-25"}, call_id=1), "s")
        gate.handle(call("remove_group_members", {"group": "g-25"}, call_id=2), "s")
        response = gate.handle(call("remove_group_members", {"group": "g-25"}, call_id=3), "s")
    finally:
        client.close()

    assert first is not None and "isError" not in first["result"]
    assert upstream.tools_called == ["remove_group_members"] * 2, "only the third is stopped"
    assert response is not None
    text = response["result"]["content"][0]["text"]
    assert "cumulative ceiling of 50" in text
    assert "session total is" in text
    assert "declared ceiling" not in text, "attributed the block to a per-call ceiling"


def test_unresolvable_target_explains_itself(tenant: SyntheticTenant) -> None:
    gate, upstream, client = build(tenant, Mode.ENFORCE)
    try:
        response = gate.handle(call("send_email", {"to": "g-ddg"}))
    finally:
        client.close()
    assert upstream.tools_called == []
    assert response is not None
    text = response["result"]["content"][0]["text"]
    assert "could not be sized" in text
    assert response["result"]["_meta"]["neti"]["resolved"] is None


# --------------------------------------------------------------- observe mode


def test_observe_mode_forwards_everything_including_blocks(tenant: SyntheticTenant) -> None:
    """What makes the install reversible: worst case is a proxy hop."""
    gate, upstream, client = build(tenant, Mode.OBSERVE)
    try:
        response = gate.handle(call("remove_group_members", {"group": "g-eng-all"}))
    finally:
        client.close()

    assert upstream.tools_called == ["remove_group_members"]
    assert response is not None
    assert "isError" not in response["result"]
    assert gate.stats["verdict.block"] == 1
    assert gate.stats.get("stopped", 0) == 0


# --------------------------------------------------------------- passthrough


@pytest.mark.parametrize(
    "method",
    ["initialize", "tools/list", "resources/list", "prompts/get", "completion/complete"],
)
def test_non_tool_calls_pass_through(tenant: SyntheticTenant, method: str) -> None:
    """Failing closed on unrecognised methods would break protocol negotiation and the session."""
    gate, upstream, client = build(tenant, Mode.ENFORCE)
    try:
        message = {"jsonrpc": "2.0", "id": 7, "method": method, "params": {}}
        gate.handle(message)
    finally:
        client.close()
    assert upstream.sent == [message]


def test_notifications_pass_through_and_return_nothing(tenant: SyntheticTenant) -> None:
    gate, upstream, client = build(tenant, Mode.ENFORCE)
    try:
        message = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert gate.handle(message) is None
    finally:
        client.close()
    assert upstream.sent == [message]


def test_ungated_tool_passes_through(tenant: SyntheticTenant) -> None:
    gate, upstream, client = build(tenant, Mode.ENFORCE)
    try:
        gate.handle(call("read_calendar", {"id": "x"}))
    finally:
        client.close()
    assert upstream.tools_called == ["read_calendar"]


def test_malformed_tool_call_is_left_to_the_server(tenant: SyntheticTenant) -> None:
    """Inventing our own envelope error would mask the server's, which is more specific."""
    gate, upstream, client = build(tenant, Mode.ENFORCE)
    try:
        message = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": 42}}
        gate.handle(message)
    finally:
        client.close()
    assert upstream.sent == [message]


# --------------------------------------------------------------- sessions


def test_session_id_is_threaded_through_and_scopes_budgets(tenant: SyntheticTenant) -> None:
    for i in range(260):
        tenant.add(Group(f"g-one-{i}", f"Solo {i}", transitive_members=1))

    gate, upstream, client = build(tenant, Mode.ENFORCE)
    try:
        stopped_at = None
        for i in range(260):
            resp = gate.handle(call("send_email", {"to": f"g-one-{i}"}, call_id=i), "sess-a")
            if resp is not None and resp["result"].get("isError") and stopped_at is None:
                stopped_at = i
        # a different session is unaffected by the first one's spend
        other = gate.handle(call("send_email", {"to": "g-one-0"}, call_id=999), "sess-b")
    finally:
        client.close()

    assert stopped_at == 200, "the cumulative recipients budget did not fire where declared"
    assert other is not None
    assert "isError" not in other["result"]
    assert upstream.sessions[0] == "sess-a"


# --------------------------------------------------------------- records


def test_every_gated_call_is_recorded_and_the_chain_verifies(
    tenant: SyntheticTenant, tmp_path: Any
) -> None:
    path = tmp_path / "decisions.ndjson"
    with JsonlSink(path) as sink:
        gate, _, client = build(tenant, Mode.OBSERVE, sink=sink)
        try:
            for group in ("g-solo", "g-team", "g-eng-all"):
                gate.handle(call("send_email", {"to": group}), "s")
            gate.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {}})
        finally:
            client.close()

    records = list(read_records(path))
    assert len(records) == 3, "tools/list must not produce a decision record"

    from neti.core.record import verify_chain

    ok, bad = verify_chain(records)
    assert ok and bad is None

    verdicts = [r.verdict for r in records]
    assert verdicts == ["allow", "allow", "block"]


def test_records_survive_a_round_trip_byte_identically(
    tenant: SyntheticTenant, tmp_path: Any
) -> None:
    """Serialisation must not perturb the chained bytes, or `neti verify` is worthless."""
    path = tmp_path / "d.ndjson"
    with JsonlSink(path) as sink:
        gate, _, client = build(tenant, Mode.OBSERVE, sink=sink)
        try:
            gate.handle(call("remove_group_members", {"group": "g-eng-all"}), "s")
        finally:
            client.close()

    from neti.core.record import chain_digest

    record = next(read_records(path))
    assert chain_digest(record.prev_digest, record.chained) == record.record_digest


def test_a_corrupt_record_line_raises_rather_than_being_skipped(tmp_path: Any) -> None:
    """Silently stepping over a bad line would make a chain break unexplainable."""
    path = tmp_path / "d.ndjson"
    path.write_text('{"not": "a record"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable decision record"):
        list(read_records(path))


def test_stats_track_the_observe_to_enforce_metric(tenant: SyntheticTenant) -> None:
    gate, _, client = build(tenant, Mode.ENFORCE)
    try:
        gate.handle(call("send_email", {"to": "g-solo"}))
        gate.handle(call("send_email", {"to": "g-eng-all"}))
    finally:
        client.close()
    assert gate.stats["decisions"] == 2
    assert gate.stats["stopped"] == 1
    assert gate.stats[f"verdict.{Verdict.ALLOW.name.lower()}"] == 1
