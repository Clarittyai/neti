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
class DeclinedParam:
    """A parameter this did *not* gate, and why not.

    The generated policy used to say `# not sized: count, offset, query` and stop there, which reads
    as "no resolver exists for these" — true for `count` and `offset`, and misleading for `query`,
    where a resolver exists and the tool's context argues against using it. An operator cannot
    review a judgement they cannot see.
    """

    param: str
    why: str

    would_be: str | None = None
    """The resolver whose name-rule matched and whose context test failed.

    This is the field `eval/surveys/mcp_coverage.py` was maintaining by hand, one directory over,
    as `_SIZABLE_IN_PRINCIPLE` — a flat parameter-name-to-resolver map with no context test at all.
    Against the servers people actually install it claimed 43 tools were sizeable, of which roughly
    two thirds were wrong: every `owner` on a per-issue GitHub call, every `repo` (the resolver
    needs `owner/repo` and a JSON pointer resolves one value), and every `query` on a *search*
    server. Derived here instead, so the survey and the matcher cannot disagree.
    """


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params: tuple[str, ...]
    gated: tuple[GatedParam, ...]
    destructive: bool
    declined: tuple[DeclinedParam, ...] = ()

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

    **`server.env` is passed.** It was parsed out of the client config and then read by nothing, so
    every credentialed server — Slack, GitHub, Notion, Stripe, Drive — exited at startup saying
    which variable it wanted, and `neti init` reported it as un-introspectable. That is most of the
    SaaS surface, and the operator's own config had the token in it the whole time. The same dead
    field as `providers:`, one directory over; found by running discovery against a config that
    looked like somebody's machine.

    The gate path never had this bug: there the *client* launches `neti gate -- …` and applies the
    `env` block to the gate process, which the child inherits. Only discovery, which launches
    servers itself, had to carry the environment by hand.
    """
    from concurrent.futures import TimeoutError as FutureTimeout

    from neti.gateway.stdio import StdioUpstream

    upstream = StdioUpstream(
        server.argv, timeout_s=timeout_s, echo_stderr=False, env=server.env or None
    )
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
#
# A parameter is worth gating when a resolver can turn it into a count. This was two regexes naming
# the two Entra resolvers, which meant `neti init` could only ever propose 2 of the 10 resolvers
# that ship — and against the MCP servers people actually install it gated **0 of 160** discovered
# tools, 43 of which carried a parameter some shipped resolver could size.
#
# The reason it is a table and not more branches is that `eval/surveys/mcp_coverage.py` derives
# "which resolvers can `init` ever propose" by *running* the matcher. A table makes that number
# reviewable; an if/elif chain makes it something nobody can count.
#
# **A parameter name alone is not evidence.** The hand-written map this replaces claimed 43 tools
# were sizeable and was wrong about roughly two thirds of them, always in the same direction — it
# matched a name and asked nothing else. `query` is a SQL statement on a database server and a
# search string on six other servers. `path` is a path on this disk on a filesystem server and a
# path *inside a repository* on GitHub. `owner` names a whole organisation, which is the right
# thing to size when the org is the target and irrelevant when the call touches one issue in it.
# So every rule that needs context carries a test for it, and a rule whose test fails declines with
# its reason written down rather than falling silently through.

_DESTRUCTIVE = re.compile(r"(remove|delete|revoke|purge|disable|deprovision|offboard|wipe)", re.I)


@dataclass(frozen=True)
class Rule:
    """One reason to believe a parameter names a set some shipped resolver can size."""

    resolver: str
    param: re.Pattern[str]
    """Anchored. A rule that fires on `output_path` because it contains `path` is precisely the
    over-claim this file exists not to make."""

    why: str
    """Written into the generated policy for a human to read, so it states the assumption rather
    than the resolver — `neti init` is inferring, and it should say what it inferred."""

    tool: re.Pattern[str] | None = None
    not_tool: re.Pattern[str] | None = None
    needs_siblings: frozenset[str] = frozenset()
    forbids_siblings: frozenset[str] = frozenset()
    """The discriminator a tool name cannot supply. GitHub's `create_or_update_file` takes `path`,
    and the only evidence that it means a path inside a repository is the company it keeps."""

    destructive_only: bool = False
    unit: str | None = None

    also: tuple[tuple[str, str, str], ...] = ()
    """Further `(suffix, resolver, why)` units off the same target, when the tool is destructive.

    A tuple rather than a boolean flag because `eval/surveys/mcp_coverage.py` derives "which
    resolvers can `init` ever propose" from this table, and a resolver reachable only through a
    hard-coded branch inside the matcher is one that derivation cannot see — `entra.apps` was
    reported as never-proposed while being proposed on every destructive group call."""

    declined_why: str = ""
    """What to tell the operator when the name matched and the context test did not."""

    needs_env: tuple[str, ...] = ()
    """What the resolver needs before it can answer at all. Surfaced next to the gate, because a
    destructive tool bound to a resolver with no credential becomes a hard denial the day somebody
    turns on enforce."""


RULES: tuple[Rule, ...] = (
    # ---------------------------------------------------------------- Entra
    # Unchanged, and first in the table on purpose: `neti init`'s existing output is pinned by
    # `tests/integration/test_discover.py` and by the golden transcripts, down to pointer order and
    # the wording of each `why`. Widening the matcher must not move any of it.
    Rule(
        resolver="entra.principals",
        param=re.compile(r"^(group|group_?id|groupId|team|distribution_?list|dl|list)$", re.I),
        why="names a directory group, so its transitive membership is one $count away",
        # The second unit from the same target. Losing access to 37 applications is a different harm
        # from 41,203 people losing it, and only one of them is measured in people. Emitted for
        # anything destructive pointed at a group, not just membership changes: deleting a group
        # destroys its app assignments every bit as much as emptying it does.
        also=(("#apps", "entra.apps", "the applications that same group grants access to"),),
    ),
    Rule(
        resolver="entra.principals",
        param=re.compile(r"^(to|recipients?|cc|bcc|audience)$", re.I),
        # Units belong to the parameter's role, not the resolver, and session_budgets aggregate by
        # unit — this line is what makes a recipients budget apply at all.
        unit="recipients",
        why="a delivery target: the resolver counts principals, here recipients",
    ),
    # ---------------------------------------------------------------- the filesystem
    Rule(
        resolver="fs.paths",
        # Anything ending in `path` or `paths`. Enumerating the spellings missed `notebook_path`
        # on Claude Code's own `NotebookEdit` — a plain local file, on the runtime this product
        # gates most often. Every path-ish parameter across 170 real tool schemas is a genuine
        # local path (`filePath`, `outputDirPath`, `requestFilePath`, `notebook_path`), so the
        # suffix is the honest rule and the list was an accident of which ones got written down.
        param=re.compile(r"^[A-Za-z]*_?paths?$|^(directory|dir)$", re.I),
        why="a path on this machine, which a capped local walk can count",
        # GitHub's file tools take `path` too, and there it means a path inside a repository — a
        # local walk would count something else entirely and report it with a straight face.
        forbids_siblings=frozenset({"owner", "repo", "repository"}),
        declined_why="a path inside a repository, not on this disk — fs.paths would count the "
        "wrong tree",
    ),
    Rule(
        resolver="fs.paths",
        param=re.compile(r"^(source|destination|src|dest)$", re.I),
        why="a path on this machine, which a capped local walk can count",
        # A move or a copy names two paths and either can be a directory. The object-store rule
        # below claims the same names, so this one declines wherever the tool says it is addressing
        # a bucket — the two are disjoint by construction rather than by ordering luck.
        not_tool=re.compile(r"(s3|bucket|object|blob|storage|sync)", re.I),
        forbids_siblings=frozenset({"owner", "repo", "repository"}),
        declined_why="a path inside a repository, not on this disk — fs.paths would count the "
        "wrong tree",
    ),
    Rule(
        resolver="fs.paths",
        param=re.compile(r"^(pattern|glob|globs)$", re.I),
        why="a glob, and one short glob is the whole tree",
        # Only where the tool says it globs. `search_files` and `Grep` also take `pattern` and there
        # it is a *content* pattern — matching text, not naming files — so gating it would size a
        # set the parameter does not describe. Their `path` is gated by the rule above, which is the
        # parameter that actually bounds the call.
        tool=re.compile(r"glob", re.I),
        declined_why="a search pattern rather than a set of paths; the tool's path parameter is "
        "what bounds this call",
    ),
    # ---------------------------------------------------------------- databases
    Rule(
        resolver="db.rows",
        param=re.compile(r"^(sql|statement)$", re.I),
        why="a statement whose predicate `select count(*)` can size before it runs",
        needs_env=("NETI_DATABASE_URL",),
    ),
    Rule(
        resolver="db.rows",
        param=re.compile(r"^query$", re.I),
        why="a statement whose predicate `select count(*)` can size before it runs",
        # `query` is the most over-claimed parameter name there is. On the servers people install it
        # is a web search, a knowledge-graph lookup, a documentation lookup and a repository search
        # far more often than it is SQL — and `db.rows` would decline every one of those, but only
        # after an operator had committed a gate to it and read `UNRESOLVED` on every call.
        tool=re.compile(r"(sql|query|db|database|postgres|sqlite|mysql|warehouse)", re.I),
        not_tool=re.compile(r"(search|find|browse|fetch|resolve|docs|web|node|repositor)", re.I),
        declined_why="a search string on this tool, not a SQL statement",
        needs_env=("NETI_DATABASE_URL",),
    ),
    # ---------------------------------------------------------------- object stores
    Rule(
        resolver="storage.objects",
        param=re.compile(r"^(bucket|prefix)$", re.I),
        why="an object-store prefix, counted by listing it up to a cap",
        needs_env=("AWS_ACCESS_KEY_ID",),
    ),
    Rule(
        resolver="storage.objects",
        param=re.compile(r"^(uri|url|source|destination|src|dest)$", re.I),
        why="an `s3://bucket/prefix` target, counted by listing it up to a cap",
        tool=re.compile(r"(s3|bucket|object|blob|storage)", re.I),
        declined_why="a URL, and nothing here says it addresses an object store",
        needs_env=("AWS_ACCESS_KEY_ID",),
    ),
    # ---------------------------------------------------------------- infrastructure
    Rule(
        resolver="terraform.destroy",
        param=re.compile(r"^(plan|plan_?json|plan_?file)$", re.I),
        why="a Terraform plan, whose destroy and replace counts are in the document",
        tool=re.compile(r"(terraform|tofu|tf_|apply|plan)", re.I),
        declined_why="nothing here says this is a Terraform plan",
    ),
    # ---------------------------------------------------------------- source control
    Rule(
        resolver="github.repos",
        param=re.compile(r"^(owner|org|organisation|organization)$", re.I),
        why="a bare owner, which addresses every repository under it",
        # Only when the owner *is* the target. `create_issue(owner, repo, …)` takes an owner too
        # and touches one issue in one repository; sizing the organisation there produces a large,
        # confident number about something the call does not do.
        forbids_siblings=frozenset({"repo", "repository"}),
        destructive_only=True,
        declined_why="the call names a repository, so the owner is an address and not the target",
        needs_env=("NETI_GITHUB_TOKEN",),
    ),
)


# Resolvers that ship and that `neti init` will never propose, each with the reason. An empty
# `never_proposed` is the honest way this metric finishes: every resolver is either proposable or
# has a written reason it is not, rather than a silent gap.
NEVER_PROPOSED: dict[str, str] = {
    "entra.guests": (
        "a sub-count of entra.principals. Propose the total and band the guest share as a "
        "breakdown, rather than gating the same target twice"
    ),
    "entra.principals_with_guests": (
        "two Graph requests, not one. `entra.principals` staying a single O(1) count is the "
        "latency claim the design rests on, so the operator opts into paying for the split"
    ),
    "github.files": (
        "needs `owner/repo`, and a JSON pointer resolves one value. `/repo` alone yields `api`, "
        "which the resolver would read as an *owner* and answer with that organisation's "
        "repository count — a confident number about the wrong thing. There is no way to express "
        "the join in the pointer language today"
    ),
}


def _applies(rule: Rule, tool: str, params: set[str], destructive: bool) -> bool:
    if rule.tool is not None and not rule.tool.search(tool):
        return False
    if rule.not_tool is not None and rule.not_tool.search(tool):
        return False
    if rule.destructive_only and not destructive:
        return False
    if rule.needs_siblings and not rule.needs_siblings <= params:
        return False
    return not (rule.forbids_siblings & params)


def match(tool: str, params: list[str]) -> tuple[GatedParam, ...]:
    """Which of a tool's parameters name a set whose size a resolver can produce."""
    return _adjudicate(tool, params)[0]


def _adjudicate(
    tool: str, params: list[str]
) -> tuple[tuple[GatedParam, ...], tuple[DeclinedParam, ...]]:
    """Every parameter, sorted into gated-with-a-reason or declined-with-a-reason.

    Nothing falls through unremarked. A parameter is either something we will size, or something we
    looked at and decided against, and the operator reading the generated policy gets to see which.
    """
    destructive = bool(_DESTRUCTIVE.search(tool))
    present = set(params)
    gated: list[GatedParam] = []
    declined: list[DeclinedParam] = []

    for param in params:
        named = [rule for rule in RULES if rule.param.match(param)]
        taken = next((r for r in named if _applies(r, tool, present, destructive)), None)

        if taken is None:
            near = next((r for r in named if r.declined_why), None)
            declined.append(
                DeclinedParam(
                    param=param,
                    why=near.declined_why if near else "no resolver claims this parameter",
                    would_be=near.resolver if near else None,
                )
            )
            continue

        gated.append(
            GatedParam(pointer=f"/{param}", resolver=taken.resolver, unit=taken.unit, why=taken.why)
        )
        if destructive:
            gated += [
                GatedParam(pointer=f"/{param}{suffix}", resolver=resolver, unit=None, why=why)
                for suffix, resolver, why in taken.also
            ]

    return tuple(gated), tuple(declined)


def classify(raw: dict[str, Any]) -> ToolSpec:
    name = str(raw.get("name", ""))
    schema = raw.get("inputSchema") or {}
    props = schema.get("properties") if isinstance(schema, dict) else None
    params = sorted(props) if isinstance(props, dict) else []
    gated, declined = _adjudicate(name, params)
    return ToolSpec(
        name=name,
        description=str(raw.get("description", "")).strip().splitlines()[0][:100]
        if raw.get("description")
        else "",
        params=tuple(params),
        gated=gated,
        destructive=bool(_DESTRUCTIVE.search(name)),
        declined=declined,
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


def _providers_block(found: Discovery, tenant_env: str) -> list[str]:
    """Only the provider blocks the proposed resolvers actually use.

    This emitted `providers.entra` unconditionally, which was harmless while the matcher could only
    ever propose Entra resolvers and is not any more. A machine whose every gate is `fs.paths` would
    otherwise be handed a policy demanding `NETI_TENANT_ID` for a directory it does not have, to
    feed resolvers nothing in the file binds — and the first thing an operator does with a generated
    file is try to run it.
    """
    proposed = {g.resolver for tool in found.gated for g in tool.gated}
    lines = ["providers:"]

    if any(name.startswith("entra.") for name in proposed):
        lines += [
            "  entra:",
            f"    tenant_id: {tenant_env}",
            "    auth: client_credentials # GroupMember.Read.All, admin-consented. Read-only.",
            "    timeout_ms: 800",
            "    consistency: eventual",
        ]
    if "fs.paths" in proposed:
        lines += [
            "  fs:",
            "    # The tree the agent is allowed into. Without it `fs.paths` still sizes every",
            "    # call; this is what lets `neti inventory` report reach rather than `?`.",
            "    root: .",
            "    cap: 200000 # a latency control: past this the answer is a floor, never a total",
        ]
    if "github.repos" in proposed or "github.files" in proposed:
        lines += ["  github: {} # set `owner:` to have `neti inventory` report reachable repos"]
    if "db.rows" in proposed:
        lines += ["  db: {} # needs NETI_DATABASE_URL, pointed at a read-only user"]
    if "storage.objects" in proposed:
        lines += ["  storage: {} # needs the usual AWS credentials, read-only"]

    if len(lines) == 1:
        # `providers: {}` rather than a bare `providers:`, which parses as null and is refused at
        # load — the failure mode this whole file exists to avoid handing somebody.
        lines = ["providers: {}"]
    return [*lines, ""]


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
        *_providers_block(found, tenant_env),
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
            # The judgement calls first, one line each. A parameter some resolver's name-rule
            # matched and whose context argued against it is the thing an operator most needs to be
            # able to overrule, and it used to be indistinguishable from `count` and `offset` in a
            # single comma-separated list that read as "no resolver exists for any of these".
            for declined in tool.declined:
                if declined.would_be:
                    lines.append(
                        f"      # not sized: {declined.param} — {declined.why}"
                        f" (would have been {declined.would_be})"
                    )
            plain = [d.param for d in tool.declined if not d.would_be]
            if plain:
                lines.append(
                    f"      # not sized: {', '.join(plain)} — no resolver claims these parameters"
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
