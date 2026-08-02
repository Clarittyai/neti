"""M10 — of the MCP servers people actually install, how many tools can `neti` size?

`neti init` has never been run against a config that looks like somebody's machine. The README's
example shows one server exposing four tools, three of which are gated, and there is no reason to
think that ratio survives contact with a real config. This measures it: write a `.mcp.json` carrying
the catalogue, run the same `find_clients` → `list_tools` → `classify` path `neti init` runs, and
count.

**Ungated is the number that matters.** A gated tool is a claim `neti` is making; an ungated one
is a tool it has taken no responsibility for (SCOPE.md NC-09). A survey that reported only the
first would let a reader infer that everything absent is safe, which is backwards — the same
`src/neti/eval/stack.py` makes about its `uncovered` layer, applied to servers.

Two counts are reported and they are not the same question:

- **gated** — parameters `neti init`'s matcher claims today.
- **sizable in principle** — parameters some *shipped* resolver could size if the matcher knew about
  it. The gap between these two is a defect in `insight/discover.py`, not a missing resolver, and
  keeping them apart is what makes the difference legible instead of being averaged into one number.

Everything here is read-only: servers are launched, asked `tools/list`, and killed.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from eval.surveys.catalogue import CATALOGUE, Candidate
from neti.insight.discover import (
    _INIT,
    DiscoveryError,
    ServerSpec,
    classify,
    find_clients,
    match,
)

__all__ = ["ServerResult", "Survey", "run"]

RESULTS = Path(__file__).resolve().parents[1] / "results" / "mcp_coverage.json"


# ---------------------------------------------------------------------------- what a shipped
# resolver could size, if the matcher knew to propose it.
#
# Deliberately a *hand-written* mapping from parameter name to resolver rather than anything clever.
# The claim it supports is narrow — "a resolver that ships could produce a count for this parameter"
# — and a heuristic that guessed would turn a measurement into an opinion. Each entry names the
# resolver in `resolvers_for_client()` that would take it.
_SIZABLE_IN_PRINCIPLE: dict[str, str] = {
    "path": "fs.paths",
    "paths": "fs.paths",
    "file_path": "fs.paths",
    "directory": "fs.paths",
    "dir": "fs.paths",
    "root": "fs.paths",
    "pattern": "fs.paths",
    "repository": "fs.paths",
    "repo": "github.files",
    "owner": "github.repos",
    "org": "github.repos",
    "sql": "db.rows",
    "query": "db.rows",
    "statement": "db.rows",
    "bucket": "storage.objects",
    "prefix": "storage.objects",
    "plan": "terraform.destroy",
}


@dataclass
class ToolResult:
    name: str
    params: list[str]
    gated: list[dict[str, str]]
    ungated: list[str]
    sizable_in_principle: dict[str, str] = field(default_factory=dict)
    destructive: bool = False


@dataclass
class ServerResult:
    name: str
    argv: list[str]
    layer: str
    transport: str
    note: str
    archived: bool
    placeholder_credential: bool
    launched: bool
    error: str | None = None
    tools: list[ToolResult] = field(default_factory=list)

    @property
    def gated_tools(self) -> int:
        return sum(1 for t in self.tools if t.gated)

    @property
    def sizable_tools(self) -> int:
        return sum(1 for t in self.tools if t.sizable_in_principle)


@dataclass
class Survey:
    metric: str
    platform: str
    servers: list[ServerResult]
    registry: dict[str, Any]

    @property
    def launched(self) -> list[ServerResult]:
        return [s for s in self.servers if s.launched]

    def totals(self) -> dict[str, int]:
        tools = [t for s in self.launched for t in s.tools]
        return {
            "servers_in_catalogue": len(self.servers),
            "servers_launched": len(self.launched),
            "servers_remote": sum(1 for s in self.servers if s.transport == "remote"),
            "servers_failed": sum(
                1 for s in self.servers if not s.launched and s.transport != "remote"
            ),
            "tools_discovered": len(tools),
            "tools_gated": sum(1 for t in tools if t.gated),
            "tools_sizable_in_principle": sum(1 for t in tools if t.sizable_in_principle),
            "tools_ungated": sum(1 for t in tools if not t.gated),
        }


# ---------------------------------------------------------------------------- the registry gap


def _registry() -> dict[str, Any]:
    """Which resolvers ship, and which of them `neti init` is capable of ever proposing.

    Derived rather than asserted: the proposable set is read out of `insight/discover.match` by
    running it over the parameter names its own regexes are built from, so this cannot go stale the
    way a hand-maintained list would.
    """
    from neti.eval.synthetic import default_tenant
    from neti.resolvers.graph_client import ClientCredential, GraphClient
    from neti.resolvers.registry import resolvers_for_client

    cred = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")
    client = GraphClient(cred, transport=default_tenant().transport())
    registered = sorted(resolvers_for_client(client))
    probe = ["group", "team", "to", "recipients", "cc", "bcc", "distribution_list", "list"]
    proposable = sorted({g.resolver for g in match("delete_group", probe)})
    return {
        "registered": registered,
        "init_can_propose": proposable,
        "never_proposed": sorted(set(registered) - set(proposable)),
    }


# ---------------------------------------------------------------------------- probing


def _write_config(root: Path, candidates: tuple[Candidate, ...]) -> Path:
    """A `.mcp.json` in the shape every stdio client uses, so `find_clients` reads it for real."""
    workdir = root / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "README.md").write_text("survey fixture\n")
    db = root / "fixture.sqlite"
    db.touch()

    # `mcp-server-git` refuses to start outside a repository, and a harness that could not produce
    # one would have published "could not launch" against a server that launches perfectly well.
    # A survey's failure column is only worth reading if every entry in it is the server's fault.
    with contextlib.suppress(Exception):
        subprocess.run(
            ["git", "init", "--quiet", str(workdir)],
            check=True,
            capture_output=True,
            timeout=30,
        )

    servers: dict[str, Any] = {}
    for c in candidates:
        if c.transport != "stdio":
            continue
        argv = [a.format(dir=str(workdir), db=str(db)) for a in c.argv]
        servers[c.name] = {
            "command": argv[0],
            "args": argv[1:],
            **({"env": c.env} if c.env else {}),
        }
    path = root / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}, indent=2))
    return path


def _list_tools_with_stderr(spec: ServerSpec, timeout_s: float) -> list[dict[str, Any]]:
    """`discover.list_tools`, but keeping the server's stderr when it fails.

    `list_tools` folds stderr into its message only on the timeout path, so everything else arrives
    as "the MCP server behind the gate closed its output" — true, and useless in a table whose whole
    subject is *why* a server could not be introspected. Four of the reference servers turn out to
    die on an upstream SDK incompatibility and three more on a rejected credential, and those are
    different facts that deserve to be reported as different facts.
    """
    from neti.gateway.stdio import StdioUpstream

    upstream = StdioUpstream(
        spec.argv, timeout_s=timeout_s, echo_stderr=False, env=spec.env or None
    )
    try:
        upstream.send(_INIT, None)
        upstream.send({"jsonrpc": "2.0", "method": "notifications/initialized"}, None)
        response = upstream.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, None)
    except Exception as exc:
        said = " | ".join(upstream.stderr_tail[-3:]).strip()
        raise DiscoveryError(f"{exc}{f' — it said: {said}' if said else ''}") from exc
    finally:
        upstream.close()

    if not response or "result" not in response:
        raise DiscoveryError(f"no tools/list result: {json.dumps(response)[:200]}")
    tools = response["result"].get("tools")
    return [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []


def _probe(spec: ServerSpec, candidate: Candidate, timeout_s: float) -> ServerResult:
    result = ServerResult(
        name=candidate.name,
        argv=list(spec.argv),
        layer=candidate.layer,
        transport=candidate.transport,
        note=candidate.note,
        archived=candidate.archived,
        placeholder_credential=candidate.placeholder_credential,
        launched=False,
    )
    try:
        raw = _list_tools_with_stderr(spec, timeout_s=timeout_s)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}".strip()
        return result

    result.launched = True
    for tool in raw:
        spec_ = classify(tool)
        result.tools.append(
            ToolResult(
                name=spec_.name,
                params=list(spec_.params),
                gated=[{"pointer": g.pointer, "resolver": g.resolver} for g in spec_.gated],
                ungated=list(spec_.ungated),
                sizable_in_principle={
                    p: _SIZABLE_IN_PRINCIPLE[p.lower()]
                    for p in spec_.ungated
                    if p.lower() in _SIZABLE_IN_PRINCIPLE
                },
                destructive=spec_.destructive,
            )
        )
    return result


def run(
    candidates: tuple[Candidate, ...] = CATALOGUE,
    *,
    timeout_s: float = 90.0,
    only: str | None = None,
) -> Survey:
    chosen = tuple(c for c in candidates if only is None or c.name == only)
    with tempfile.TemporaryDirectory(prefix="neti-m10-") as tmp:
        root = Path(tmp)
        _write_config(root, chosen)
        # The real discovery path, pointed at an isolated home so the operator's own servers do not
        # leak into a published measurement.
        specs = {s.name: s for s in find_clients(cwd=root, home=root)}

        results: list[ServerResult] = []
        for candidate in chosen:
            if candidate.transport != "stdio":
                results.append(
                    ServerResult(
                        name=candidate.name,
                        argv=[],
                        layer=candidate.layer,
                        transport=candidate.transport,
                        note=candidate.note,
                        archived=candidate.archived,
                        placeholder_credential=candidate.placeholder_credential,
                        launched=False,
                        error="remote transport: no command for `neti init` to launch",
                    )
                )
                continue
            spec = specs.get(candidate.name)
            if spec is None:
                results.append(
                    ServerResult(
                        name=candidate.name,
                        argv=list(candidate.argv),
                        layer=candidate.layer,
                        transport=candidate.transport,
                        note=candidate.note,
                        archived=candidate.archived,
                        placeholder_credential=candidate.placeholder_credential,
                        launched=False,
                        error="find_clients did not return this server",
                    )
                )
                continue
            print(f"  probing {candidate.name} …", file=sys.stderr, flush=True)
            results.append(_probe(spec, candidate, timeout_s))

    return Survey(
        metric="M10",
        platform=f"{platform.system()} {platform.machine()}",
        servers=results,
        registry=_registry(),
    )


# ---------------------------------------------------------------------------- output


def as_markdown(survey: Survey) -> str:
    t = survey.totals()
    out = [
        "## M10 — coverage against the MCP servers people actually install",
        "",
        f"{t['servers_launched']} of {t['servers_in_catalogue']} catalogued servers launched "
        f"({t['servers_remote']} remote, {t['servers_failed']} failed). "
        f"**{t['tools_gated']} of {t['tools_discovered']} discovered tools are gated by "
        f"`neti init`**; {t['tools_sizable_in_principle']} more carry a parameter a shipped "
        "resolver could size if the matcher proposed it.",
        "",
        "| server | layer | tools | gated | sizable in principle | note |",
        "|---|---|---:|---:|---:|---|",
    ]
    for s in survey.servers:
        if not s.launched:
            out.append(f"| `{s.name}` | {s.layer} | — | — | — | **could not launch** — {s.error} |")
            continue
        out.append(
            f"| `{s.name}` | {s.layer} | {len(s.tools)} | {s.gated_tools} | "
            f"{s.sizable_tools} | {s.note} |"
        )
    reg = survey.registry
    out += [
        "",
        f"`neti init` can only ever propose **{len(reg['init_can_propose'])} of "
        f"{len(reg['registered'])}** registered resolvers "
        f"({', '.join(reg['init_can_propose'])}). Never proposed: "
        f"{', '.join(f'`{r}`' for r in reg['never_proposed'])}.",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--markdown", action="store_true", help="print the table instead of JSON")
    parser.add_argument("--only", help="probe a single catalogue entry, by name")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args(argv)

    for tool in ("npx", "uvx"):
        if shutil.which(tool) is None:
            print(f"needs {tool} on PATH to launch real servers", file=sys.stderr)
            return 2

    survey = run(timeout_s=args.timeout, only=args.only)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {**asdict(survey), "totals": survey.totals()}
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(as_markdown(survey) if args.markdown else f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
