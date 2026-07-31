"""`neti init` — find the agent's tools and write a starting policy.

The wall between installing this and getting anything out of it was a YAML file the operator had to
author from nothing: tool names they would have to look up, JSON pointers into argument shapes they
would have to read, a resolver per parameter, a unit per role. That is a day of work standing in
front of a finding that takes one command to produce, and most people will not do it.

So this reads the MCP client configs they already have, launches each server exactly the way the
client does, asks it `tools/list`, and writes a policy that matches their actual tools.

**It declares no ceilings.** Not one. Every gate it emits has an empty `bands:` list, which resolves
and records and cannot block anything — and `mode:` is `observe` besides. That is not timidity, it
is the product's own doctrine: a ceiling nobody chose is a ceiling nobody will defend the first time
it fires. The numbers are supposed to arrive a week later from `neti propose`, out of the operator's
own traffic. A tool that guessed them here would be the one dishonest thing in the codebase.

What it does decide is the mechanical part nobody should have to hand-write: which parameter is a
target worth sizing, which resolver can size it, and what unit it is in for that tool's role.
Everything it could not match is written into the file as a comment, because a parameter this cannot
size is exactly the thing an operator needs to see rather than not see.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "Discovery",
    "DiscoveryError",
    "GatedParam",
    "ServerSpec",
    "ToolSpec",
    "find_clients",
    "list_tools",
    "render_policy",
]


class DiscoveryError(Exception):
    """A server that could not be introspected, with a reason an operator can act on."""


# ---------------------------------------------------------------------------- what we found


@dataclass(frozen=True)
class ServerSpec:
    """One stdio MCP server, as some client is configured to launch it."""

    client: str
    """Which client's config this came from, for the operator to recognise."""

    path: Path
    name: str
    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)

    @property
    def argv(self) -> list[str]:
        return [self.command, *self.args]


@dataclass(frozen=True)
class GatedParam:
    pointer: str
    resolver: str
    unit: str | None
    why: str


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params: tuple[str, ...]
    gated: tuple[GatedParam, ...]
    destructive: bool

    @property
    def ungated(self) -> tuple[str, ...]:
        sized = {g.pointer.lstrip("/").split("#")[0] for g in self.gated}
        return tuple(p for p in self.params if p not in sized)


@dataclass(frozen=True)
class Discovery:
    servers: tuple[ServerSpec, ...]
    tools: tuple[ToolSpec, ...]
    errors: tuple[str, ...] = ()

    @property
    def gated(self) -> tuple[ToolSpec, ...]:
        return tuple(t for t in self.tools if t.gated)


# ---------------------------------------------------------------------------- client configs


# Every place a stdio MCP server is normally declared. Read-only, and a missing file is the common
# case rather than an error — most machines have one or two of these, never all of them.
def _candidates(cwd: Path, home: Path) -> list[tuple[str, Path]]:
    found = [
        ("Claude Code (project)", cwd / ".mcp.json"),
        ("Claude Code (user)", home / ".claude.json"),
        ("Cursor (project)", cwd / ".cursor" / "mcp.json"),
        ("Cursor (user)", home / ".cursor" / "mcp.json"),
        ("VS Code", cwd / ".vscode" / "mcp.json"),
    ]
    if sys.platform == "darwin":
        found.append(
            (
                "Claude Desktop",
                home / "Library/Application Support/Claude/claude_desktop_config.json",
            )
        )
    elif sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            found.append(("Claude Desktop", Path(appdata) / "Claude/claude_desktop_config.json"))
    else:
        found.append(("Claude Desktop", home / ".config/Claude/claude_desktop_config.json"))
    return found


def find_clients(
    cwd: Path | None = None, home: Path | None = None, *, already_gated: list[str] | None = None
) -> list[ServerSpec]:
    """Every stdio MCP server the machine's agent clients are configured to launch.

    Servers already wrapped by `neti gate` are skipped — re-wrapping a gate in a gate would double
    every decision and write each one to the chain twice — but their names are appended to
    `already_gated` rather than vanishing. Silently dropping them made `neti init` announce "No MCP
    servers found" to an operator who had just gated all of theirs, which reads as discovery being
    broken rather than as work already done.
    """
    cwd = cwd or Path.cwd()
    home = home or Path.home()
    out: list[ServerSpec] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    for label, path in _candidates(cwd, home):
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue

        for name, spec in _servers_in(raw):
            command = spec.get("command")
            if not isinstance(command, str) or not command:
                continue  # an HTTP/SSE server, or malformed — neither is ours to launch
            args = [str(a) for a in spec.get("args", []) if isinstance(a, (str, int))]
            if command == "neti" or "neti" in Path(command).name:
                if already_gated is not None:
                    already_gated.append(name)
                continue
            key = (command, tuple(args))
            if key in seen:
                continue
            seen.add(key)
            env = {k: str(v) for k, v in (spec.get("env") or {}).items()}
            out.append(
                ServerSpec(client=label, path=path, name=name, command=command, args=args, env=env)
            )
    return out


def _servers_in(raw: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """`mcpServers` at the top level, or nested per project the way Claude Code stores it."""
    found: list[tuple[str, dict[str, Any]]] = []
    top = raw.get("mcpServers") or raw.get("servers")
    if isinstance(top, dict):
        found += [(n, s) for n, s in top.items() if isinstance(s, dict)]

    projects = raw.get("projects")
    if isinstance(projects, dict):
        for project in projects.values():
            if isinstance(project, dict):
                nested = project.get("mcpServers")
                if isinstance(nested, dict):
                    found += [(n, s) for n, s in nested.items() if isinstance(s, dict)]
    return found


# ---------------------------------------------------------------------------- asking the server

_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "neti-init", "version": "0.1.0"},
    },
}


def list_tools(server: ServerSpec, *, timeout_s: float = 20.0) -> list[dict[str, Any]]:
    """Launch the server the way its client would and ask what tools it exposes.

    Uses the same `StdioUpstream` the gate runs on, so a server that cannot be introspected here is
    one the gate could not have wrapped either — better to find that out now than at install time.
    """
    from concurrent.futures import TimeoutError as FutureTimeout

    from neti.gateway.stdio import StdioUpstream

    upstream = StdioUpstream(server.argv, timeout_s=timeout_s, echo_stderr=False)
    try:
        try:
            upstream.send(_INIT, None)
            upstream.send({"jsonrpc": "2.0", "method": "notifications/initialized"}, None)
            response = upstream.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, None)
        except FutureTimeout as exc:
            # `TimeoutError()` stringifies to the empty string, so reporting it verbatim gives the
            # operator "could not introspect entra ()" — a message that names no cause and suggests
            # no fix. Say which step stalled and how long we waited.
            said = " | ".join(upstream.stderr_tail[-3:])
            raise DiscoveryError(
                f"no answer within {timeout_s:.0f}s. The command may not be an MCP server, "
                "or it may be waiting on something — try running it by hand."
                + (f" It last said: {said}" if said else "")
            ) from exc
    finally:
        upstream.close()

    if not response or "result" not in response:
        return []
    tools = response["result"].get("tools")
    return [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []


# ---------------------------------------------------------------------------- matching

# A parameter is worth gating when a resolver can turn it into a count. These are the two Entra
# resolvers that exist; the table grows as resolvers do, and anything unmatched is reported rather
# than quietly dropped.
_GROUPISH = re.compile(r"^(group|group_?id|groupId|team|distribution_?list|dl|list)$", re.I)
_RECIPIENTS = re.compile(r"^(to|recipients?|cc|bcc|audience)$", re.I)
_DESTRUCTIVE = re.compile(r"(remove|delete|revoke|purge|disable|deprovision|offboard|wipe)", re.I)


def match(tool: str, params: list[str]) -> tuple[GatedParam, ...]:
    """Which of a tool's parameters name a set whose size a resolver can produce."""
    destructive = bool(_DESTRUCTIVE.search(tool))
    gated: list[GatedParam] = []

    for param in params:
        if _GROUPISH.match(param):
            gated.append(
                GatedParam(
                    pointer=f"/{param}",
                    resolver="entra.principals",
                    unit=None,
                    why="names a directory group, so its transitive membership is one $count away",
                )
            )
            # The second unit from the same target. Losing access to 37 applications is a different
            # harm from 41,203 people losing it, and only one of them is measured in people.
            #
            # Emitted for anything destructive pointed at a group, not just membership changes:
            # deleting a group destroys its app assignments every bit as much as emptying it does.
            # In observe mode with no ceiling this costs one extra O(1) read and buys the operator a
            # number they would otherwise never see.
            if destructive:
                gated.append(
                    GatedParam(
                        pointer=f"/{param}#apps",
                        resolver="entra.apps",
                        unit=None,
                        why="the applications that same group grants access to",
                    )
                )
        elif _RECIPIENTS.match(param):
            gated.append(
                GatedParam(
                    pointer=f"/{param}",
                    resolver="entra.principals",
                    unit="recipients",
                    # Units belong to the parameter's role, not the resolver, and session_budgets
                    # aggregate by unit — this line is what makes a recipients budget apply at all.
                    why="a delivery target: the resolver counts principals, here recipients",
                )
            )

    return tuple(gated)


def classify(raw: dict[str, Any]) -> ToolSpec:
    name = str(raw.get("name", ""))
    schema = raw.get("inputSchema") or {}
    props = schema.get("properties") if isinstance(schema, dict) else None
    params = sorted(props) if isinstance(props, dict) else []
    return ToolSpec(
        name=name,
        description=str(raw.get("description", "")).strip().splitlines()[0][:100]
        if raw.get("description")
        else "",
        params=tuple(params),
        gated=match(name, params),
        destructive=bool(_DESTRUCTIVE.search(name)),
    )


def discover(servers: list[ServerSpec], *, probe: bool = True) -> Discovery:
    tools: list[ToolSpec] = []
    errors: list[str] = []
    if probe:
        for server in servers:
            try:
                tools += [classify(t) for t in list_tools(server)]
            except Exception as exc:
                errors.append(f"{server.name} ({' '.join(server.argv)}): {exc}")
    # Same tool from two clients is one tool.
    unique = {t.name: t for t in tools if t.name}
    return Discovery(servers=tuple(servers), tools=tuple(unique.values()), errors=tuple(errors))


# ---------------------------------------------------------------------------- the file


def render_policy(found: Discovery, *, tenant_env: str = "${NETI_TENANT_ID}") -> str:
    """Write the policy as YAML, by hand.

    Serialised by hand rather than through the YAML dumper because half the value of this file is
    the commentary: which parameter was matched and why, what was left ungated, and — loudest — that
    every ceiling is still blank on purpose. A dumper would produce a correct file nobody reads.
    """
    lines: list[str] = [
        "# Generated by `neti init`. Read it before you trust it.",
        "#",
        "# Every gate below has an EMPTY `bands:` list, which means it resolves the target and",
        "# records the magnitude and cannot block anything. That is deliberate, and it is the",
        "# whole method: run a week like this, then `neti report` and `neti propose`, and",
        "# declare ceilings from your own traffic. A ceiling nobody chose is one nobody will",
        "# defend the first time it fires.",
        "",
        "version: 1",
        "mode: observe # observe cannot block. Move to enforce once the numbers below are yours.",
        "",
        "providers:",
        "  entra:",
        f"    tenant_id: {tenant_env}",
        "    auth: client_credentials # GroupMember.Read.All, admin-consented. Read-only.",
        "    timeout_ms: 800",
        "    consistency: eventual",
        "",
    ]

    gated = found.gated
    if not gated:
        lines += [
            "# Nothing here yet: no discovered tool had a parameter a resolver knows how to size.",
            "# Add tools by hand below, or see RESOLVER_CONTRACT.md to write a resolver for the",
            "# parameter you care about.",
            "tools: {}",
            "",
        ]
    else:
        lines.append("tools:")
        for tool in sorted(gated, key=lambda t: t.name):
            if tool.description:
                lines.append(f"  # {tool.description}")
            lines.append(f"  {tool.name}:")
            lines.append("    gate:")
            for g in tool.gated:
                lines.append(f"      {g.pointer}:")
                lines.append(f"        # {g.why}")
                lines.append(f"        resolver: {g.resolver}")
                if g.unit:
                    lines.append(f"        unit: {g.unit}")
                lines.append("        bands: [] # <- your numbers go here, from your own traffic")
                lines.append(
                    f"        on_unresolved: {'block' if tool.destructive else 'confirm'}"
                    " # a failed lookup is never read as zero"
                )
            if tool.ungated:
                lines.append(
                    f"      # not sized: {', '.join(tool.ungated)}"
                    " — no resolver claims these parameters"
                )
            lines.append("")

    skipped = [t for t in found.tools if not t.gated]
    if skipped:
        lines += [
            "# Discovered but not gated. An ungated tool is out of scope, not denied (SCOPE.md",
            "# NC-09). Listed so you can see what this did NOT take responsibility for:",
        ]
        lines += [f"#   {t.name}" for t in sorted(skipped, key=lambda t: t.name)]
        lines.append("")

    lines += [
        "session_budgets:",
        "  # A per-call ceiling is structurally blind to four thousand individual sends: each one",
        "  # resolves to 1 and passes (SCOPE.md NC-01). Only a declared cumulative total sees it.",
        "  # Left empty for the same reason the bands above are — declare it, do not inherit it.",
        "  []",
        "",
        "defaults:",
        "  unknown_tool: allow # out of scope, not denied",
        "",
    ]
    return "\n".join(lines)
