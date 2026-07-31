"""The `neti` command line.

The order these appear in `--help` is the order an operator meets them: measure the provider,
inventory what the credential can reach, run the gate in observe mode, report what happened, propose
ceilings from that, and verify the record chain.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

app = typer.Typer(
    add_completion=False,
    help="A preflight gate for agent tool calls: resolve what an action will touch, "
    "block it if it exceeds a declared ceiling.",
)


@app.command()
def measure(
    group: Annotated[
        list[str],
        typer.Option("--group", "-g", help="Entra group object id. Pass several, spanning sizes."),
    ],
    repeat: Annotated[
        int, typer.Option(help="Samples per group, after one discarded warm-up.")
    ] = 30,
    timeout_ms: Annotated[int, typer.Option(help="Per-request timeout.")] = 800,
) -> None:
    """Measure Graph resolution latency against a real tenant.

    Needs NETI_TENANT_ID, NETI_CLIENT_ID and NETI_CLIENT_SECRET, and an app with
    GroupMember.Read.All. Read-only. Pass at least one small and one large group so the
    latency-is-flat-in-magnitude claim can actually be tested.
    """
    from neti.insight.measure import MeasureError, format_report
    from neti.insight.measure import measure as run

    try:
        out = run(list(group), repeat=repeat, timeout_ms=timeout_ms)
    except MeasureError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    typer.echo(format_report(out))


@app.command()
def check(
    group: Annotated[
        list[str],
        typer.Option(
            "--group",
            "-g",
            help="Group object id or mail address. Pass at least two of very different sizes.",
        ),
    ],
    repeat: Annotated[int, typer.Option(help="Latency samples per group.")] = 20,
    timeout_ms: Annotated[int, typer.Option()] = 800,
) -> None:
    """Answer the four tenant-side questions the project is blocked on, in one command.

    Needs NETI_TENANT_ID, NETI_CLIENT_ID and NETI_CLIENT_SECRET, and an Entra app with
    GroupMember.Read.All (application permission, admin-consented). Read-only throughout.

    Pass at least one small and one large group: the claim under test is that a 40,000-member
    group costs the same to size as a 3-member one.
    """
    import httpx

    from neti.eval.tenant_checks import format_checks, run_checks
    from neti.resolvers.graph_client import ClientCredential, GraphClient, GraphError

    missing = [
        name
        for name in ("NETI_TENANT_ID", "NETI_CLIENT_ID", "NETI_CLIENT_SECRET")
        if not os.environ.get(name)
    ]
    if missing:
        typer.secho(f"error: not set: {', '.join(missing)}", fg=typer.colors.RED, err=True)
        typer.echo(
            "\nRegister an Entra app, grant Microsoft Graph > Application > GroupMember.Read.All,\n"
            "grant admin consent, add a client secret, then export the three variables.",
            err=True,
        )
        raise typer.Exit(2)

    credential = ClientCredential(
        tenant_id=os.environ["NETI_TENANT_ID"],
        client_id=os.environ["NETI_CLIENT_ID"],
        client_secret=os.environ["NETI_CLIENT_SECRET"],
    )
    client = GraphClient(credential, timeout_ms=timeout_ms)
    raw = httpx.Client(timeout=timeout_ms / 1000)
    try:
        try:
            token = client.token_for_checks()
        except GraphError as exc:
            typer.secho(f"error: could not authenticate: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(2) from exc
        results = run_checks(client, raw, token, list(group), repeat=repeat)
    finally:
        raw.close()
        client.close()

    typer.echo(format_checks(results))


@app.command()
def inventory(
    config: Annotated[
        str, typer.Option("--config", "-c", help="Policy file. See examples/entra.yaml.")
    ] = "neti.yaml",
    timeout_ms: Annotated[int, typer.Option(help="Per-request timeout.")] = 800,
) -> None:
    """What could each gated tool reach in one call? No traffic and no ceilings required.

    The hour-one finding: "your agent holds a credential that can, in one call, remove 41,203
    people from a group." Needs NETI_TENANT_ID, NETI_CLIENT_ID and NETI_CLIENT_SECRET.
    """
    from neti.config.policy import PolicyError, load_policy
    from neti.insight.inventory import build_inventory, format_inventory
    from neti.resolvers.base import ResolveContext, ResolverError
    from neti.resolvers.registry import build_entra_resolvers

    try:
        policy = load_policy(config)
        resolvers, client = build_entra_resolvers(timeout_ms=timeout_ms)
    except (PolicyError, OSError, ResolverError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    try:
        rows = build_inventory(policy, resolvers, ResolveContext(timeout_ms=timeout_ms))
    finally:
        client.close()

    typer.echo(format_inventory(rows))


def _apply_mode(policy: Any, override: str | None) -> Any:
    """Let the command line override the policy's `mode:`.

    The override goes through the policy object rather than around it, so the digest sealed into
    every record changes with it. That is the honest behaviour: the same ceilings in observe and in
    enforce are not the same policy, and a record that claimed otherwise would let a decision be
    replayed under a mode that never produced it.
    """
    from neti.core.verdict import Mode

    if override is None:
        return policy
    try:
        return policy.model_copy(update={"mode": Mode[override.upper()]})
    except KeyError as exc:
        typer.secho(
            f"error: --mode must be observe or enforce, not {override!r}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2) from exc


def _build_resolvers(*, demo: bool, timeout_ms: int) -> tuple[Any, Any]:
    """The demo/live seam, shared by every command that gates.

    `Engine`, `decide` and the records are the same objects either way; only the transport under the
    Graph client differs. That is what lets `--demo` be an honest rehearsal of the install rather
    than a separate code path that drifts from it.
    """
    from neti.eval.synthetic import default_tenant
    from neti.resolvers.graph_client import ClientCredential, GraphClient
    from neti.resolvers.registry import build_entra_resolvers, resolvers_for_client

    if not demo:
        return build_entra_resolvers(timeout_ms=timeout_ms)

    credential = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")
    client = GraphClient(credential, transport=default_tenant().transport(), timeout_ms=timeout_ms)
    return resolvers_for_client(client), client


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def gate(
    ctx: typer.Context,
    upstream: Annotated[
        str | None, typer.Option("--upstream", "-u", help="MCP server URL to sit in front of.")
    ] = None,
    stdio: Annotated[
        bool,
        typer.Option(
            "--stdio",
            help="Wrap a local MCP server launched over stdio. Put its command after `--`.",
        ),
    ] = False,
    config: Annotated[str, typer.Option("--config", "-c")] = "neti.yaml",
    records: Annotated[str, typer.Option("--records", "-r")] = "out/decisions.ndjson",
    demo: Annotated[
        bool, typer.Option("--demo", help="Resolve against the synthetic tenant. No credentials.")
    ] = False,
    mode_override: Annotated[
        str | None,
        typer.Option("--mode", help="Override the policy's mode: observe or enforce."),
    ] = None,
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8722,
    timeout_ms: Annotated[int, typer.Option()] = 800,
) -> None:
    """Run the gate in front of an MCP server.

    Two transports, because MCP has two and everyone uses the local one:

        neti gate --stdio -- npx -y @acme/entra-mcp     # in your client's config, as the command
        neti gate --upstream https://mcp.internal/rpc   # point the client here instead

    The policy's `mode:` decides whether anything can be blocked; the shipped example is `observe`,
    which records verdicts and forwards every call.
    """
    from neti.config.policy import PolicyError, load_policy
    from neti.engine import Engine
    from neti.gateway.mcp import McpGateway
    from neti.resolvers.base import ResolveContext, ResolverError
    from neti.store.jsonl import JsonlSink, chain_head

    argv = list(ctx.args)
    if stdio == bool(upstream):
        typer.secho(
            "error: choose one transport — `--stdio -- <command>` or `--upstream <url>`",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    if stdio and not argv:
        typer.secho(
            "error: --stdio needs the server command, after `--`\n"
            "       e.g. neti gate --stdio -- npx -y @acme/entra-mcp",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    try:
        policy = _apply_mode(load_policy(config), mode_override)
        resolvers, client = _build_resolvers(demo=demo, timeout_ms=timeout_ms)
    except (PolicyError, OSError, ResolverError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    engine = Engine(
        policy=policy,
        resolvers=resolvers,
        ctx=ResolveContext(timeout_ms=timeout_ms),
        # Continue the existing file's chain rather than starting a new one, or every restart
        # writes a mid-chain break that `neti verify` correctly reports.
        last_digest=chain_head(records),
    )
    sink = JsonlSink(records)
    mode = policy.mode.name.lower()

    if stdio:
        _gate_stdio(engine, sink, client, argv, mode, config, policy.digest(), records)
        return

    from neti.gateway.server import serve
    from neti.gateway.upstream import HttpUpstream

    gateway = McpGateway(engine=engine, upstream=HttpUpstream(str(upstream)), sink=sink)
    typer.secho(f"neti gate on http://{host}:{port}  ->  {upstream}", fg=typer.colors.GREEN)
    blurb = "  (nothing will be blocked)" if mode == "observe" else ""
    typer.echo(f"  mode:    {mode}{blurb}")
    typer.echo(f"  policy:  {config}  digest {policy.digest()[:12]}")
    typer.echo(f"  records: {records}")
    try:
        serve(gateway, host, port)
    except KeyboardInterrupt:
        typer.echo("\nstopping")
    finally:
        sink.close()
        client.close()


def _gate_stdio(
    engine: Any,
    sink: Any,
    client: Any,
    argv: list[str],
    mode: str,
    config: str,
    digest: str,
    records: str,
) -> None:
    """The stdio path, kept apart for one reason: stdout is the protocol here.

    Not a single byte of the banner above may reach stdout in this mode — the client is parsing it
    as JSON-RPC, and one friendly line desynchronises the session. Everything goes to stderr, where
    the client shows it as server logs.
    """
    import sys

    from neti.gateway.mcp import McpGateway
    from neti.gateway.stdio import StdioUpstream, serve_stdio

    print(f"neti gate (stdio)  ->  {' '.join(argv)}", file=sys.stderr)
    blurb = "  (nothing will be blocked)" if mode == "observe" else ""
    print(f"  mode:    {mode}{blurb}", file=sys.stderr)
    print(f"  policy:  {config}  digest {digest[:12]}", file=sys.stderr)
    print(f"  records: {records}", file=sys.stderr)

    gateway: McpGateway | None = None
    upstream: StdioUpstream | None = None
    try:
        upstream = StdioUpstream(argv)
        gateway = McpGateway(engine=engine, upstream=upstream, sink=sink)
        # `upstream=` hands the child's server→client traffic to the same locked writer.
        serve_stdio(gateway, upstream=upstream)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError) as exc:
        print(f"neti: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    finally:
        if upstream is not None:
            upstream.close()
        sink.close()
        client.close()
        if gateway is not None:
            stopped = gateway.stats.get("stopped", 0)
            total = gateway.stats.get("decisions", 0)
            print(f"neti: {total} gated, {stopped} stopped", file=sys.stderr)


@app.command()
def hook(
    config: Annotated[str, typer.Option("--config", "-c")] = "neti.yaml",
    records: Annotated[str, typer.Option("--records", "-r")] = "out/decisions.ndjson",
    demo: Annotated[
        bool, typer.Option("--demo", help="Resolve against the synthetic tenant. No credentials.")
    ] = False,
    mode_override: Annotated[
        str | None,
        typer.Option("--mode", help="Override the policy's mode: observe or enforce."),
    ] = None,
    timeout_ms: Annotated[int, typer.Option()] = 800,
) -> None:
    """Gate a Claude Code `PreToolUse` event read from stdin.

    For the calls no proxy can see — the harness's own built-in tools. Wire it in `settings.json`:

        {"hooks": {"PreToolUse": [{"matcher": "*",
          "hooks": [{"type": "command", "command": "neti hook"}]}]}}

    A pass says nothing at all, which leaves your existing permission rules exactly as they were.
    """
    import sys

    from neti.adapters.claude_code import read_event, run_hook
    from neti.config.policy import PolicyError, load_policy
    from neti.engine import Engine
    from neti.resolvers.base import ResolveContext, ResolverError
    from neti.store.jsonl import JsonlSink, chain_head

    try:
        event = read_event(sys.stdin.read())
        policy = _apply_mode(load_policy(config), mode_override)
        resolvers, client = _build_resolvers(demo=demo, timeout_ms=timeout_ms)
    except (PolicyError, OSError, ResolverError, ValueError, json.JSONDecodeError) as exc:
        # A hook that cannot run must not take the session down with it. Say why on stderr, exit 0,
        # and let the call proceed under whatever rules were already in place — failing closed here
        # would block every tool in the session the moment a credential expired.
        print(f"neti hook: {exc}", file=sys.stderr)
        raise typer.Exit(0) from exc

    engine = Engine(
        policy=policy,
        resolvers=resolvers,
        ctx=ResolveContext(timeout_ms=timeout_ms),
        last_digest=chain_head(records),
    )
    sink = JsonlSink(records)
    try:
        response = run_hook(engine, event, sink)
    finally:
        sink.close()
        client.close()

    if response:
        typer.echo(json.dumps(response))


@app.command()
def report(
    records: Annotated[str, typer.Option("--records", "-r")] = "out/decisions.ndjson",
) -> None:
    """What your agents already did: observed magnitudes, and the calls that exceeded a ceiling."""
    from neti.insight.report import build_report, format_report
    from neti.store.jsonl import read_records

    try:
        summary = build_report(read_records(records))
    except (OSError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    typer.echo(format_report(summary))


@app.command()
def propose(
    records: Annotated[str, typer.Option("--records", "-r")] = "out/decisions.ndjson",
) -> None:
    """Suggest ceilings from your own observed traffic, for a human to review and commit.

    Output is text to edit into a policy file. Nothing here is applied automatically, and nothing
    computed here is ever read at decision time.
    """
    from neti.insight.propose import format_proposals
    from neti.insight.propose import propose as build
    from neti.insight.report import build_report
    from neti.store.jsonl import read_records

    try:
        summary = build_report(read_records(records))
    except (OSError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    typer.echo(format_proposals(build(summary)))


@app.command()
def serve(
    config: Annotated[str, typer.Option("--config", "-c")] = "examples/entra.yaml",
    records: Annotated[str, typer.Option("--records", "-r")] = "out/console.ndjson",
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8722,
    demo: Annotated[
        bool | None,
        typer.Option("--demo/--live", help="Force the fixture or a real tenant."),
    ] = None,
) -> None:
    """Run the console API.

    With no credentials this comes up on the synthetic fixture, because a demo that needs a tenant
    before it will start is not a demo. Export NETI_TENANT_ID / NETI_CLIENT_ID /
    NETI_CLIENT_SECRET and it talks to Microsoft instead — same engine, same decision procedure,
    same records.
    """
    try:
        import uvicorn

        from neti.api.app import create_app
    except ImportError as exc:
        typer.secho(
            "error: the console extra is not installed — run: uv pip install -e '.[console]'",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2) from exc

    from neti.api.state import build_state

    try:
        state = build_state(config=config, records=records, demo=demo)
    except (OSError, RuntimeError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    typer.secho(f"neti console API on http://{host}:{port}", fg=typer.colors.GREEN)
    typer.echo(f"  mode:    {state.mode}  ({state.tenant_label})")
    typer.echo(f"  policy:  {config}  digest {state.policy.digest()[:12]}")
    typer.echo(f"  records: {records}")
    typer.echo("  web ui:  cd web && npm run dev   ->  http://localhost:3100")
    try:
        uvicorn.run(create_app(state), host=host, port=port, log_level="warning")
    finally:
        state.close()


@app.command()
def score(
    records: Annotated[str, typer.Option("--records", "-r")] = "out/decisions.ndjson",
    config: Annotated[str, typer.Option("--config", "-c")] = "neti.yaml",
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable output.")] = False,
) -> None:
    """The scorecard: incident replay, friction, blind spots, and what is not yet measured.

    Runs entirely offline. Records and policy are optional — without them you still get the
    incident replay and the non-coverage list, which is most of what an audience asks about.
    """
    from neti.config.policy import PolicyError, load_policy
    from neti.eval.scorecard import build_scorecard, format_scorecard, scorecard_json
    from neti.insight.report import build_report
    from neti.store.jsonl import read_records

    # No traffic and no policy are normal states here, not errors: the incident replay and the
    # blind-spot list are useful before anything is configured, which is most of the point.
    summary = None
    with contextlib.suppress(OSError, ValueError):
        summary = build_report(read_records(records))

    policy = None
    with contextlib.suppress(PolicyError, OSError):
        policy = load_policy(config)

    card = build_scorecard(summary, policy)
    typer.echo(scorecard_json(card) if as_json else format_scorecard(card))


@app.command()
def verify(
    records: Annotated[str, typer.Option("--records", "-r")] = "out/decisions.ndjson",
) -> None:
    """Replay every decision and verify the hash chain."""
    from neti.core.record import verify_chain
    from neti.store.jsonl import read_records

    try:
        chain = list(read_records(records))
    except (OSError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    ok, bad = verify_chain(chain)
    if ok:
        typer.secho(f"{len(chain):,} records, chain intact", fg=typer.colors.GREEN)
        if chain:
            typer.echo(f"head: {chain[-1].record_digest}")
        return
    typer.secho(f"CHAIN BROKEN at decision {bad}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@app.command()
def demo(
    config: Annotated[str, typer.Option("--config", "-c")] = "examples/entra.yaml",
    out: Annotated[str, typer.Option("--out", "-o", help="Write JSON here.")] = "-",
) -> None:
    """Run the whole narrative against the synthetic tenant and emit it as JSON.

    Every number is produced by the real decision path, so the demo cannot drift from the product.
    The data is synthetic and the output says so — it demonstrates behaviour, not a finding.
    """
    from neti.eval.demo import demo_json

    payload = demo_json(config)
    if out == "-":
        typer.echo(payload)
        return
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    typer.secho(f"wrote {path} ({len(payload):,} bytes)", fg=typer.colors.GREEN)


@app.command()
def version() -> None:
    from neti import __version__

    typer.echo(__version__)


def main() -> int:
    app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
