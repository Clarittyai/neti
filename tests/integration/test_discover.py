"""`neti init` — the command that has to work before anything else can.

The assertion that matters most is the round trip: what this writes must load as a policy and gate a
real call. A generator that emits plausible YAML the loader then rejects is worse than no generator,
because the operator hits the failure after they have already decided to trust it.

The second is that it declares nothing. Every band it writes is empty, and a test says so out loud —
if someone later "helpfully" seeds a default ceiling, that is the test that should stop them.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from neti.config.policy import load_policy
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.synthetic import default_tenant
from neti.insight.discover import (
    ServerSpec,
    classify,
    discover,
    find_clients,
    list_tools,
    match,
    render_policy,
)
from neti.preflight import Preflight
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client

# A server that answers tools/list the way a directory server would.
SERVER = textwrap.dedent(
    """
    import json, sys
    TOOLS = [
      {"name": "remove_group_members", "description": "Remove everyone in a group.",
       "inputSchema": {"type": "object", "properties": {"group": {"type": "string"},
                                                        "reason": {"type": "string"}}}},
      {"name": "send_email",
       "inputSchema": {"type": "object", "properties": {"to": {"type": "string"},
                                                        "subject": {"type": "string"}}}},
      {"name": "read_group",
       "inputSchema": {"type": "object", "properties": {"group": {"type": "string"}}}},
      {"name": "get_weather",
       "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}}}
    ]
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        m = json.loads(line)
        if "id" not in m: continue
        result = {"tools": TOOLS} if m.get("method") == "tools/list" else {"ok": True}
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": result}), flush=True)
    """
)


@pytest.fixture
def server() -> ServerSpec:
    return ServerSpec(
        client="test", path=Path("x"), name="entra", command=sys.executable, args=["-c", SERVER]
    )


# ---------------------------------------------------------------------------- reading configs


def test_finds_servers_across_client_configs(tmp_path: Path) -> None:
    cwd, home = tmp_path / "proj", tmp_path / "home"
    (cwd / ".cursor").mkdir(parents=True)
    home.mkdir()
    (cwd / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"entra": {"command": "npx", "args": ["-y", "@acme/entra"]}}})
    )
    (cwd / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"jira": {"command": "uvx", "args": ["jira-mcp"]}}})
    )

    found = find_clients(cwd=cwd, home=home)
    assert {s.name for s in found} == {"entra", "jira"}
    assert next(s for s in found if s.name == "entra").argv == ["npx", "-y", "@acme/entra"]


def test_finds_servers_nested_per_project(tmp_path: Path) -> None:
    """Claude Code's user config keys servers under the project they belong to."""
    cwd, home = tmp_path / "proj", tmp_path / "home"
    cwd.mkdir(parents=True)
    home.mkdir()
    (home / ".claude.json").write_text(
        json.dumps({"projects": {"/some/path": {"mcpServers": {"gh": {"command": "gh-mcp"}}}}})
    )
    assert [s.name for s in find_clients(cwd=cwd, home=home)] == ["gh"]


def test_skips_a_server_already_behind_the_gate(tmp_path: Path) -> None:
    """Wrapping a gate in a gate would double every decision and the record chain with it."""
    cwd, home = tmp_path / "proj", tmp_path / "home"
    cwd.mkdir(parents=True)
    home.mkdir()
    (cwd / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "wrapped": {"command": "neti", "args": ["gate", "--stdio", "--", "npx", "x"]},
                    "bare": {"command": "npx", "args": ["y"]},
                }
            }
        )
    )
    assert [s.name for s in find_clients(cwd=cwd, home=home)] == ["bare"]


def test_ignores_remote_servers_and_junk(tmp_path: Path) -> None:
    cwd, home = tmp_path / "proj", tmp_path / "home"
    cwd.mkdir(parents=True)
    home.mkdir()
    (cwd / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"remote": {"url": "https://mcp.example/rpc"}}})
    )
    (cwd / ".vscode").mkdir()
    (cwd / ".vscode" / "mcp.json").write_text("{ not json")
    assert find_clients(cwd=cwd, home=home) == []


# ---------------------------------------------------------------------------- asking the server


def test_lists_tools_from_a_real_server(server: ServerSpec) -> None:
    names = {t["name"] for t in list_tools(server)}
    assert names == {"remove_group_members", "send_email", "read_group", "get_weather"}


def test_a_broken_server_does_not_end_the_scan(tmp_path: Path, server: ServerSpec) -> None:
    broken = ServerSpec(
        client="test", path=Path("x"), name="broken", command=sys.executable, args=["-c", "exit(1)"]
    )
    found = discover([broken, server])
    assert found.errors  # reported, not swallowed
    assert {t.name for t in found.tools} >= {"send_email"}


# ---------------------------------------------------------------------------- matching


@pytest.mark.parametrize(
    ("tool", "params", "expected"),
    [
        ("remove_group_members", ["group"], ["/group", "/group#apps"]),
        # Deleting a group destroys its app assignments as surely as emptying it does.
        ("delete_group", ["group_id"], ["/group_id", "/group_id#apps"]),
        ("send_email", ["to", "subject"], ["/to"]),
        ("notify", ["recipients"], ["/recipients"]),
        # Sizeable, and not destructive: gating a read is the operator's call to make.
        ("read_group", ["group"], ["/group"]),
        ("get_weather", ["city"], []),
    ],
)
def test_matches_parameters_a_resolver_can_size(
    tool: str, params: list[str], expected: list[str]
) -> None:
    assert [g.pointer for g in match(tool, params)] == expected


def test_a_recipient_parameter_carries_its_unit() -> None:
    """Units belong to the role, not the resolver — and session budgets aggregate by unit."""
    (gate,) = match("send_email", ["to"])
    assert (gate.resolver, gate.unit) == ("entra.principals", "recipients")


def test_apps_are_a_second_unit_on_the_same_target() -> None:
    """41,203 people and 37 applications are different harms, and only one is measured in people."""
    pointers = {g.pointer: g.resolver for g in match("remove_group_members", ["group"])}
    assert pointers == {"/group": "entra.principals", "/group#apps": "entra.apps"}


def test_reports_the_parameters_it_could_not_size() -> None:
    spec = classify(
        {
            "name": "send_email",
            "inputSchema": {"type": "object", "properties": {"to": {}, "subject": {}, "body": {}}},
        }
    )
    assert spec.ungated == ("body", "subject")


# ---------------------------------------------------------------------------- the file


def test_declares_no_ceilings(server: ServerSpec) -> None:
    """The one thing this command must never do.

    A generated number is a number nobody chose, and nobody defends a ceiling they did not choose
    the first time it fires in front of them.
    """
    yaml = render_policy(discover([server]))
    assert "bands: []" in yaml
    assert "mode: observe" in yaml
    assert "above:" not in yaml


def test_names_what_it_did_not_take_responsibility_for(server: ServerSpec) -> None:
    yaml = render_policy(discover([server]))
    assert "get_weather" in yaml  # listed as discovered-but-ungated
    assert "not sized: reason" in yaml  # the parameter within a gated tool


def test_the_generated_policy_loads_and_gates(tmp_path: Path, server: ServerSpec) -> None:
    """The round trip. A generator whose output the loader rejects is worse than no generator."""
    config = tmp_path / "neti.yaml"
    config.write_text(render_policy(discover([server])))

    policy = load_policy(str(config))
    assert set(policy.tools) >= {"remove_group_members", "send_email"}

    client = GraphClient(
        ClientCredential(tenant_id="d", client_id="d", client_secret="d"),
        transport=default_tenant().transport(),
    )
    engine = Engine(
        policy=policy.model_copy(update={"mode": Mode.ENFORCE}),
        resolvers=resolvers_for_client(client),
    )
    verdict = Preflight(engine=engine).check("remove_group_members", {"group": "g-eng-all"})

    # It resolved and recorded the real magnitude, and — having declared no ceiling — allowed it.
    # That is the whole day-one posture: you learn the number before you choose the limit.
    assert verdict.proceeds
    assert verdict.payload["resolved"] == 41203


def test_an_empty_discovery_still_writes_a_loadable_file(tmp_path: Path) -> None:
    config = tmp_path / "neti.yaml"
    config.write_text(render_policy(discover([], probe=False)))
    assert load_policy(str(config)).tools == {}


def test_an_all_gated_machine_is_a_finished_state_not_an_empty_one(tmp_path: Path) -> None:
    """Found by running `neti init` in a project whose only server was already wrapped.

    It skipped the entry — correctly, since re-wrapping a gate in a gate doubles every decision —
    and then announced "No MCP servers found in any client config on this machine." An operator who
    had just finished gating everything was being told discovery was broken.
    """
    cwd, home = tmp_path / "proj", tmp_path / "home"
    cwd.mkdir(parents=True)
    home.mkdir()
    (cwd / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"fs": {"command": "neti", "args": ["gate", "--stdio", "--", "npx"]}}}
        )
    )

    gated: list[str] = []
    assert find_clients(cwd=cwd, home=home, already_gated=gated) == []
    assert gated == ["fs"], "a skipped server must be reported, not silently dropped"


def test_discovery_stays_quiet_about_a_server_s_startup_banner(server: ServerSpec) -> None:
    """`neti init` launches every server in turn purely to ask `tools/list`.

    Real servers print banners and npm warnings on startup. Relaying them while gating is right —
    an operator debugging their own server needs those — but during a scan they interleave with the
    progress output and make a working discovery look like it is malfunctioning. The tail is kept
    either way, so a failure can be explained in the server's own words.
    """
    from neti.gateway.stdio import StdioUpstream

    quiet = StdioUpstream(server.argv, echo_stderr=False)
    try:
        assert quiet.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, None) is not None
    finally:
        quiet.close()
    assert hasattr(quiet, "stderr_tail")


# ---------------------------------------------------------------------------- platform branches


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("darwin", {}, "Library/Application Support/Claude/claude_desktop_config.json"),
        ("win32", {"APPDATA": "/roaming"}, "/roaming/Claude/claude_desktop_config.json"),
        ("linux", {}, ".config/Claude/claude_desktop_config.json"),
    ],
)
def test_claude_desktop_is_looked_for_in_the_right_place_per_platform(
    monkeypatch: pytest.MonkeyPatch, platform: str, env: dict[str, str], expected: str
) -> None:
    """Two of these three branches have never executed anywhere.

    Everything in this repo has been developed and run on macOS, so the Windows and Linux paths for
    Claude Desktop's config were written from documentation and never once evaluated. A typo in
    either is invisible until a stranger on that platform runs `neti init` and is told, wrongly,
    that they have no MCP servers — the least debuggable possible first impression.
    """
    from pathlib import Path

    from neti.insight import discover as mod

    monkeypatch.setattr(mod.sys, "platform", platform)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    paths = [str(p) for _, p in mod._candidates(Path("/proj"), Path("/home/u"))]
    assert any(expected in p for p in paths), f"{platform}: no candidate matched {expected}"


def test_windows_without_appdata_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """`APPDATA` is all but guaranteed on Windows, and `os.environ[...]` would still be a crash on
    the one machine where it is missing. It degrades to the platform-independent locations."""
    from pathlib import Path

    from neti.insight import discover as mod

    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)

    labels = [label for label, _ in mod._candidates(Path("/proj"), Path("/home/u"))]
    assert "Claude Desktop" not in labels
    assert "Claude Code (project)" in labels


def test_every_platform_still_finds_the_project_level_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-platform branch must only ever *add*. If it replaced the common list, a Linux user
    would silently lose .mcp.json discovery — which is the one everybody actually uses."""
    from pathlib import Path

    from neti.insight import discover as mod

    for platform in ("darwin", "win32", "linux"):
        monkeypatch.setattr(mod.sys, "platform", platform)
        paths = [str(p) for _, p in mod._candidates(Path("/proj"), Path("/home/u"))]
        assert any(p.endswith("/proj/.mcp.json") for p in paths), platform
        assert any(p.endswith(".cursor/mcp.json") for p in paths), platform


def test_gating_nothing_is_explained_rather_than_left_as_a_dead_end(server: ServerSpec) -> None:
    """The likeliest first run there is, and it used to end nowhere.

    Most MCP servers are not directory servers, so `neti init` gates nothing for most people. It
    then said "next: neti inventory", and inventory said "Nothing to inventory." A stranger's five
    minutes ended in what reads as a broken tool rather than as a stated limit.

    The rendered policy has to survive that case too — an operator who reads the file must find a
    loadable document that says what happened, not an empty `tools: {}` with no explanation.
    """
    from neti.insight.discover import Discovery

    empty = Discovery(servers=(server,), tools=(), errors=())
    assert empty.gated == ()

    yaml = render_policy(empty)
    assert "tools: {}" in yaml
    assert "RESOLVER_CONTRACT.md" in yaml, "the file must point at how to close the gap"
    assert load_policy_from_text(yaml).tools == {}


def load_policy_from_text(text: str) -> Any:
    """Round-trip helper: the generated file has to actually load."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write(text)
        path = fh.name
    return load_policy(path)
