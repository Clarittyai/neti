"""The `neti` command line.

Phase 0 ships only `measure`, deliberately: it is the command that replaces modelled numbers with
measured ones, and the plan puts it before any resolver code.
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
    from neti.resolvers.base import ResolveContext
    from neti.resolvers.registry import build_entra_resolvers

    try:
        policy = load_policy(config)
    except (PolicyError, OSError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    try:
        resolvers, client = build_entra_resolvers(timeout_ms=timeout_ms)
    except Exception as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    try:
        rows = build_inventory(policy, resolvers, ResolveContext(timeout_ms=timeout_ms))
    finally:
        client.close()

    typer.echo(format_inventory(rows))


@app.command()
def version() -> None:
    from neti import __version__

    typer.echo(__version__)


def main() -> int:
    app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
