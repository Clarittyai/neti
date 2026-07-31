"""`neti-cloud` — the control plane's own command.

A separate entry point rather than `neti cloud serve`, and not for style. The free CLI lives in
`neti`, and `tests/property/test_licence_boundary.py` asserts that nothing under `src/neti/` imports
`neti_cloud` — that assertion is what makes "there are no licence checks in the Apache-2.0 code" a
fact rather than a claim. A `neti cloud` subcommand would have to import this package to register
itself, even lazily, and the boundary would be gone.

So the paid artifact ships its own command. The plan said otherwise; the test was right.
"""

from __future__ import annotations

import os
from typing import Annotated

import typer

app = typer.Typer(
    add_completion=False,
    help="The neti control plane: approvals, org policy, and audit across every agent.",
)


@app.command()
def serve(
    db: Annotated[str, typer.Option(help="SQLite file. Created if absent.")] = "neti-cloud.db",
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8730,
    key: Annotated[
        str | None, typer.Option("--key", help="Organisation key. Defaults to $NETI_CLOUD_KEY.")
    ] = None,
    ttl_s: Annotated[int, typer.Option(help="How long a request waits for a human.")] = 900,
    console_url: Annotated[str, typer.Option(help="Where reviewers read the inbox.")] = "",
    slack_token: Annotated[str | None, typer.Option(help="Slack bot token.")] = None,
    slack_channel: Annotated[str, typer.Option()] = "#neti-approvals",
    webhook_url: Annotated[str | None, typer.Option(help="POST each request here.")] = None,
) -> None:
    """Run the control plane."""
    try:
        import uvicorn
    except ImportError as exc:
        typer.secho("error: install with 'neti-cloud[server]'", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    from neti_cloud.notify import FanOut, Notifier, SlackNotifier, WebhookNotifier
    from neti_cloud.server import create_app
    from neti_cloud.store import Store

    org_key = key or os.environ.get("NETI_CLOUD_KEY")
    if not org_key:
        typer.secho(
            "error: no organisation key. Pass --key or set NETI_CLOUD_KEY.\n"
            "       It is the only thing standing between a stranger and approving your calls.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    channels: list[Notifier] = []
    if slack_token:
        channels.append(SlackNotifier(slack_token, slack_channel, console_url))
    if webhook_url:
        channels.append(WebhookNotifier(webhook_url, org_key, console_url))

    store = Store(db, ttl_s=ttl_s)
    typer.secho(f"neti control plane on http://{host}:{port}", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  store:     {db}")
    typer.echo(f"  approvals: expire after {ttl_s}s unanswered")
    named = ", ".join(type(c).__name__.removesuffix("Notifier").lower() for c in channels)
    typer.echo(f"  notify:    {named or 'console only'}")
    typer.echo(f"\n  point an agent at it:  neti login --url http://{host}:{port} --key <key>")

    try:
        uvicorn.run(
            create_app(store, org_key=org_key, notifier=FanOut(channels) if channels else None),
            host=host,
            port=port,
            log_level="warning",
        )
    except KeyboardInterrupt:
        pass
    finally:
        store.close()


@app.command()
def approve(
    approval_id: Annotated[str, typer.Argument(help="The approval to decide.")],
    deny: Annotated[bool, typer.Option("--deny", help="Refuse it instead.")] = False,
    by: Annotated[str, typer.Option(help="Who is deciding. Recorded on the grant.")] = "",
    db: Annotated[str, typer.Option()] = "neti-cloud.db",
    reason: Annotated[str, typer.Option()] = "",
) -> None:
    """Decide an approval from the command line, against the store directly.

    The console is where a reviewer normally works. This exists for the case where the console is
    not running and somebody still needs to unblock an agent.
    """
    import getpass

    from neti_cloud.store import Store

    store = Store(db)
    try:
        decided = store.decide(
            approval_id,
            granted=not deny,
            decided_by=by or getpass.getuser(),
            reason=reason or None,
        )
    finally:
        store.close()

    if decided is None:
        typer.secho(
            f"error: {approval_id} is not pending — already answered, expired, or unknown.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    typer.secho(f"{approval_id} {decided.state} by {decided.decided_by}", fg=typer.colors.GREEN)


@app.command(name="list")
def list_pending(
    db: Annotated[str, typer.Option()] = "neti-cloud.db",
    state: Annotated[str, typer.Option(help="pending | granted | denied | expired")] = "pending",
) -> None:
    """What is waiting for a human."""
    from neti_cloud.notify import summarise
    from neti_cloud.store import Store

    store = Store(db)
    try:
        rows = store.list(state or None)
    finally:
        store.close()

    if not rows:
        typer.echo(f"nothing {state}")
        return
    for row in rows:
        what, _ = summarise(row)
        typer.echo(f"  {row.id}  {what}")


def main() -> int:
    app()
    return 0
