"""The `neti` command line.

The order these appear in `--help` is the order an operator meets them: measure the provider,
inventory what the credential can reach, run the gate in observe mode, report what happened, propose
ceilings from that, and verify the record chain.
"""

from __future__ import annotations

import sys
from typing import Annotated

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


@app.command()
def gate(
    upstream: Annotated[
        str, typer.Option("--upstream", "-u", help="MCP server URL to sit in front of.")
    ],
    config: Annotated[str, typer.Option("--config", "-c")] = "neti.yaml",
    records: Annotated[str, typer.Option("--records", "-r")] = "out/decisions.ndjson",
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8722,
    timeout_ms: Annotated[int, typer.Option()] = 800,
) -> None:
    """Run the gate. Point your MCP client here instead of at the server.

    The policy's `mode:` decides whether anything can be blocked; the shipped example is `observe`,
    which records verdicts and forwards every call.
    """
    from neti.config.policy import PolicyError, load_policy
    from neti.engine import Engine
    from neti.gateway.mcp import McpGateway
    from neti.gateway.server import serve
    from neti.gateway.upstream import HttpUpstream
    from neti.resolvers.base import ResolveContext, ResolverError
    from neti.resolvers.registry import build_entra_resolvers
    from neti.store.jsonl import JsonlSink

    try:
        policy = load_policy(config)
        resolvers, client = build_entra_resolvers(timeout_ms=timeout_ms)
    except (PolicyError, OSError, ResolverError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    engine = Engine(policy=policy, resolvers=resolvers, ctx=ResolveContext(timeout_ms=timeout_ms))
    sink = JsonlSink(records)
    gateway = McpGateway(engine=engine, upstream=HttpUpstream(upstream), sink=sink)

    mode = policy.mode.name.lower()
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
def version() -> None:
    from neti import __version__

    typer.echo(__version__)


def main() -> int:
    app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
