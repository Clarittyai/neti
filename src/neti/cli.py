"""The `neti` command line.

The order these appear in `--help` is the order an operator meets them: init to find the tools and
write a policy, inventory what the credential can reach, run the gate in observe mode, report what
happened, propose ceilings from that, and verify the record chain. `measure` and `check` come first
in the list only for the operator validating a tenant before any of it.
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
def init(
    out: Annotated[
        str, typer.Option("--out", "-o", help="Where to write the policy.")
    ] = "neti.yaml",
    probe: Annotated[
        bool,
        typer.Option(help="Launch each server to ask what tools it exposes. --no-probe to skip."),
    ] = True,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing policy.")] = False,
) -> None:
    """Find your agent's tools and write a starting policy.

    Reads the MCP client configs already on this machine, launches each server the way its client
    does, and asks it what tools it exposes. Writes a policy matching the tools it found — in
    observe mode, with every ceiling left blank, because those numbers are supposed to come out of
    your own traffic a week from now and not out of this command.
    """
    from neti.insight.discover import discover, find_clients, render_policy

    target = Path(out)
    if target.exists() and not force:
        typer.secho(f"error: {out} already exists. Pass --force to overwrite.", fg="red", err=True)
        raise typer.Exit(2)

    gated: list[str] = []
    servers = find_clients(already_gated=gated)

    if not servers and gated:
        # Everything on this machine is already behind the gate. That is a finished state, not an
        # empty one, and saying "none found" would send an operator hunting for a config problem
        # they do not have.
        typer.secho(
            f"All {len(gated)} MCP server(s) here are already behind the gate: {', '.join(gated)}",
            fg=typer.colors.GREEN,
        )
        typer.echo(
            "\nNothing to do. To regenerate the policy, point --out at a new file and unwrap one\n"
            "server first, or edit the policy you already have."
        )
        raise typer.Exit(0)

    if not servers:
        typer.secho("No MCP servers found in any client config on this machine.", fg="yellow")
        typer.echo(
            "\nLooked in .mcp.json, ~/.claude.json, ~/.cursor/mcp.json, .vscode/mcp.json and the\n"
            "Claude Desktop config. If your agent's tools are not MCP servers, they can still be\n"
            "gated — see `neti hook --help` for Claude Code's built-ins, or `from neti import\n"
            "Preflight` for a tool loop you wrote yourself."
        )
        raise typer.Exit(1)

    typer.secho(f"Found {len(servers)} MCP server(s):", bold=True)
    if gated:
        typer.echo(f"  ({len(gated)} already behind the gate, skipped: {', '.join(gated)})")
    for server in servers:
        typer.echo(f"  {server.name:22} {' '.join(server.argv)[:60]}   ({server.client})")

    if probe:
        typer.echo("\nAsking each one what tools it exposes…")
    found = discover(servers, probe=probe)

    for error in found.errors:
        typer.secho(f"  could not introspect {error}", fg="yellow")

    typer.echo("")
    for tool in sorted(found.tools, key=lambda t: (not t.gated, t.name)):
        if tool.gated:
            what = ", ".join(f"{g.pointer} → {g.resolver}" for g in tool.gated)
            typer.secho(f"  gated   {tool.name:26} {what}", fg="green")
        else:
            typer.echo(f"  ungated {tool.name:26} nothing here can be sized")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_policy(found))

    typer.secho(f"\nWrote {out}", bold=True)
    example = found.servers[0] if found.servers else None
    wrap = " ".join(example.argv) if example else "<your server command>"

    if not found.gated:
        # The likeliest first run there is, and it used to end in a dead end: nothing gated, then
        # "next: neti inventory", then "Nothing to inventory." Say what is actually true — the
        # resolver catalogue does not cover these tools yet — because a stranger's read of an empty
        # result should be "I see the limit", not "this is broken".
        typer.secho(
            f"  0 of {len(found.tools)} tool(s) gated.",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        typer.echo(
            "\n  Nothing here has a parameter any shipped resolver can size. Today's catalogue is\n"
            "  filesystem paths, database rows, object-store prefixes, GitHub repos and files,\n"
            "  Entra groups (principals, apps, guests) and Terraform plans — a real limit of this\n"
            "  release, not a misconfiguration on your side.\n"
            "\n  Two things still worth doing:\n"
            f"\n    neti gate --stdio -c {out} -- {wrap[:52]}\n"
            "       run in observe anyway. Every call is recorded and sealed into the chain, so\n"
            "       `neti report` will tell you what your agents actually do — which is what you\n"
            "       would need before declaring any ceiling.\n"
            "\n    RESOLVER_CONTRACT.md\n"
            "       ~80 lines to size something these tools do touch. It is the one contribution\n"
            "       that turns this from a directory tool into one that covers your stack.\n"
        )
        return

    typer.echo(f"  {len(found.gated)} tool(s) gated, every ceiling left blank on purpose.")
    typer.secho("\nNext, in order:", bold=True)
    typer.echo(
        f"  1. neti inventory -c {out}\n"
        "       what those tools can reach right now — no traffic, no ceilings needed.\n"
        f"       Add --demo to see the shape of the answer with no credentials.\n"
        f"\n  2. neti gate --stdio -c {out} -- {wrap[:56]}\n"
        "       put that in your client's config in place of the command above, and\n"
        "       run a week in observe. Nothing is blocked; everything is recorded.\n"
        "\n  3. neti report   then   neti propose\n"
        "       ceilings out of what you actually did. Edit them in and set mode: enforce.\n"
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
        list[str] | None,
        typer.Option(
            "--group",
            "-g",
            help="Group object id or mail address. Omit and two are chosen for you.",
        ),
    ] = None,
    repeat: Annotated[int, typer.Option(help="Latency samples per group.")] = 20,
    timeout_ms: Annotated[int, typer.Option()] = 800,
    demo: Annotated[
        bool,
        typer.Option("--demo", help="Rehearse against the synthetic tenant. No credentials."),
    ] = False,
) -> None:
    """Answer the four tenant-side questions the project is blocked on, in one command.

    Needs NETI_TENANT_ID, NETI_CLIENT_ID and NETI_CLIENT_SECRET, and an Entra app with
    GroupMember.Read.All (application permission, admin-consented). Read-only throughout.

    With no `--group`, it picks the smallest and largest groups it can see. The claim under test is
    that a 40,000-member group costs the same to size as a 3-member one, so two similar groups
    cannot answer it — which is why the selection is by extremes and is printed.

    `--demo` runs the whole thing against the synthetic tenant. It cannot tell you anything about
    *your* tenant — that is the entire point of the command — but it proves this machine can run
    the check, so that when you do have credentials the only unknown left is the tenant.
    """
    import httpx

    from neti.eval.tenant_checks import discover_targets, format_checks, run_checks
    from neti.resolvers.graph_client import ClientCredential, GraphClient, GraphError

    if demo:
        from neti.eval.synthetic import default_tenant

        transport = default_tenant().transport()
        credential = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")
        client = GraphClient(credential, transport=transport, timeout_ms=timeout_ms)
        raw = httpx.Client(transport=transport, timeout=timeout_ms / 1000)
        typer.secho(
            "--demo: the synthetic tenant, not yours. This proves the check runs; it proves "
            "nothing about your directory.\n",
            fg=typer.colors.YELLOW,
        )
    else:
        missing = [
            name
            for name in ("NETI_TENANT_ID", "NETI_CLIENT_ID", "NETI_CLIENT_SECRET")
            if not os.environ.get(name)
        ]
        if missing:
            typer.secho(f"error: not set: {', '.join(missing)}", fg=typer.colors.RED, err=True)
            typer.echo(
                "\nRegister an Entra app, grant Microsoft Graph > Application > "
                "GroupMember.Read.All,\ngrant admin consent, add a client secret, then export the "
                "three variables.\n\nTo see the shape of the answer first: neti check --demo",
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
        targets = list(group or [])
        if not targets:
            targets, how = discover_targets(raw, token)
            if not targets:
                typer.secho(f"error: {how}", fg=typer.colors.RED, err=True)
                typer.echo("Pass --group with two groups of very different sizes.", err=True)
                raise typer.Exit(2)
            typer.echo(f"No --group given; {how}.\n")
        results = run_checks(client, raw, token, targets, repeat=repeat)
    finally:
        raw.close()
        client.close()

    typer.echo(format_checks(results))


@app.command()
def inventory(
    config: Annotated[
        str, typer.Option("--config", "-c", help="Policy file. See examples/entra.yaml.")
    ] = "neti.yaml",
    demo: Annotated[
        bool, typer.Option("--demo", help="Resolve against the synthetic tenant. No credentials.")
    ] = False,
    timeout_ms: Annotated[int, typer.Option(help="Per-request timeout.")] = 800,
) -> None:
    """What could each gated tool reach in one call? No traffic and no ceilings required.

    The hour-one finding: "your agent holds a credential that can, in one call, remove 41,203
    people from a group." Needs NETI_TENANT_ID, NETI_CLIENT_ID and NETI_CLIENT_SECRET, or --demo
    to see the shape of the answer against the synthetic tenant first.
    """
    from neti.config.policy import PolicyError, load_policy
    from neti.insight.inventory import build_inventory, format_inventory
    from neti.resolvers.base import ResolveContext, ResolverError

    try:
        policy = load_policy(config)
        resolvers, client = _build_resolvers(
            demo=demo,
            timeout_ms=timeout_ms,
            needs_entra=_needs_entra(policy),
            providers=policy.providers,
        )
    except (PolicyError, OSError, ResolverError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    try:
        rows = build_inventory(policy, resolvers, ResolveContext(timeout_ms=timeout_ms))
    finally:
        client.close()

    typer.echo(format_inventory(rows))


@app.command()
def login(
    url: Annotated[str, typer.Option("--url", help="Your control plane's base URL.")],
    key: Annotated[str, typer.Option("--key", help="The organisation key.")],
    org: Annotated[str, typer.Option()] = "default",
) -> None:
    """Point this machine at a control plane, for approvals and org policy.

    Everything the free tier does keeps working without this. What it adds is the one thing a single
    machine cannot do: ask somebody else to approve a call.
    """
    import httpx

    from neti.cloud import Credentials, save_credentials

    base = url.rstrip("/")
    try:
        health = httpx.get(f"{base}/v1/health", timeout=5)
        reachable = health.status_code == 200 and health.json().get("ok") is True
    except httpx.HTTPError as exc:
        typer.secho(f"error: cannot reach {base} — {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    if not reachable:
        typer.secho(f"error: {base} does not look like a neti control plane", fg="red", err=True)
        raise typer.Exit(2)

    # Health is unauthenticated, so prove the key separately — otherwise "logged in" would mean
    # "the URL resolved", and the first thing to fail would be a real approval at a bad moment.
    probe = httpx.get(f"{base}/v1/approvals", headers={"Authorization": f"Bearer {key}"}, timeout=5)
    if probe.status_code == 401:
        typer.secho("error: the control plane rejected that key", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)

    path = save_credentials(Credentials(url=base, key=key, org=org))
    typer.secho(f"Logged in to {base} as {org}", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  credentials: {path}  (mode 600 — this key can approve calls)")
    typer.echo("\n  now run the gate with --org:  neti gate --stdio --org -- <your server command>")


@app.command()
def logout() -> None:
    """Forget the control plane. The gate falls back to the free tier's behaviour."""
    from neti.cloud import credentials_path

    path = credentials_path()
    if path.exists():
        path.unlink()
        typer.echo(f"removed {path}")
    else:
        typer.echo("not logged in")


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


def _needs_entra(policy: Any) -> bool:
    """Does this policy actually bind a resolver that needs a directory credential?

    Asked because it was not, and the result was that gating a coding agent on `fs.paths` and
    `db.rows` refused to start without `NETI_TENANT_ID`. Demanding an Entra app registration from
    somebody whose policy never mentions Entra is a wall in front of the cheapest install there is.
    """
    return any(
        gate.resolver.startswith("entra.")
        for tool in policy.tools.values()
        for gate in tool.gate.values()
    )


def _build_resolvers(
    *,
    demo: bool,
    timeout_ms: int,
    needs_entra: bool = True,
    providers: dict[str, Any] | None = None,
) -> tuple[Any, Any]:
    """The demo/live seam, shared by every command that gates.

    `Engine`, `decide` and the records are the same objects either way; only the transport under the
    Graph client differs. That is what lets `--demo` be an honest rehearsal of the install rather
    than a separate code path that drifts from it.

    `providers` is the policy's own block. It is what gives `fs.paths` a root and `github.repos` an
    owner, and therefore what lets `neti inventory` report a number rather than `?` for anything
    outside Entra.
    """
    from neti.eval.synthetic import default_tenant
    from neti.resolvers.graph_client import ClientCredential, GraphClient
    from neti.resolvers.registry import build_entra_resolvers, resolvers_for_client

    if not demo and needs_entra:
        return build_entra_resolvers(timeout_ms=timeout_ms, providers=providers)

    if not demo:
        # No Entra gate in the policy, so no Entra credential is required. The registry is still
        # built whole — the entra resolvers are present but unreachable, and would fail loudly on
        # the first token fetch if a policy somehow named one. Nothing here touches the network:
        # `GraphClient` acquires its token lazily.
        blank = ClientCredential(tenant_id="", client_id="", client_secret="")
        client = GraphClient(blank, timeout_ms=timeout_ms)
        return resolvers_for_client(client, providers), client

    credential = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")
    client = GraphClient(credential, transport=default_tenant().transport(), timeout_ms=timeout_ms)
    return resolvers_for_client(client, providers), client


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
    org: Annotated[
        bool,
        typer.Option("--org", help="Escalate a CONFIRM to your control plane. Needs `neti login`."),
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
        resolvers, client = _build_resolvers(
            demo=demo,
            timeout_ms=timeout_ms,
            needs_entra=_needs_entra(policy),
            providers=policy.providers,
        )
        # Inside the try: the Engine refuses a policy that can never work — a resolver that is not
        # registered, a session budget in a unit nothing produces. Those are config mistakes and
        # deserve the same one-line error as a malformed YAML file, not a traceback.
        engine = Engine(
            policy=policy,
            resolvers=resolvers,
            ctx=ResolveContext(timeout_ms=timeout_ms),
            # Continue the existing file's chain rather than starting a new one, or every restart
            # writes a mid-chain break that `neti verify` correctly reports.
            last_digest=chain_head(records),
        )
    except (PolicyError, OSError, ResolverError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    sink = JsonlSink(records)
    mode = policy.mode.name.lower()
    approver = _approver(org)

    if stdio:
        _gate_stdio(engine, sink, client, argv, mode, config, policy.digest(), records, approver)
        return

    from neti.gateway.server import serve
    from neti.gateway.upstream import HttpUpstream

    gateway = McpGateway(
        engine=engine, upstream=HttpUpstream(str(upstream)), sink=sink, approver=approver
    )
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


def _approver(org: bool) -> Any:
    """A control plane, if the operator asked for one and is logged in.

    Refuses rather than falling back silently: someone who passed `--org` believes their `CONFIRM`s
    reach a human, and quietly running without one would leave them thinking approvals are wired up
    when every such call is simply being stopped.
    """
    if not org:
        return None

    from neti.cloud import HttpApprover, load_credentials

    creds = load_credentials()
    if creds is None or not creds.configured:
        typer.secho(
            "error: --org needs a control plane. Run `neti login --url ... --key ...` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    return HttpApprover(url=creds.url, key=creds.key)


def _gate_stdio(
    engine: Any,
    sink: Any,
    client: Any,
    argv: list[str],
    mode: str,
    config: str,
    digest: str,
    records: str,
    approver: Any = None,
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
    if approver is not None:
        print("  approvals: on — a CONFIRM asks a human", file=sys.stderr)

    gateway: McpGateway | None = None
    upstream: StdioUpstream | None = None
    try:
        upstream = StdioUpstream(argv)
        gateway = McpGateway(engine=engine, upstream=upstream, sink=sink, approver=approver)
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
    from neti.config.policy import load_policy
    from neti.engine import Engine
    from neti.resolvers.base import ResolveContext
    from neti.store.jsonl import JsonlSink, chain_head

    try:
        event = read_event(sys.stdin.read())
        policy = _apply_mode(load_policy(config), mode_override)
        resolvers, client = _build_resolvers(
            demo=demo,
            timeout_ms=timeout_ms,
            needs_entra=_needs_entra(policy),
            providers=policy.providers,
        )
        # Engine construction belongs inside this try, and it is the whole reason the net is this
        # wide. The Engine refuses a policy that can never work; if that exception escaped here it
        # would crash the hook, and a crashed PreToolUse hook takes out *every tool call in the
        # session*. A typo in a resolver name must not be able to do that.
        engine = Engine(
            policy=policy,
            resolvers=resolvers,
            ctx=ResolveContext(timeout_ms=timeout_ms),
            last_digest=chain_head(records),
        )
    except Exception as exc:
        # Deliberately every exception. A hook that cannot run must not take the session down with
        # it: say why on stderr, exit 0, and let the call proceed under whatever rules were already
        # in place. Failing closed here would block every tool the moment a credential expired.
        print(f"neti hook: {exc}", file=sys.stderr)
        raise typer.Exit(0) from exc
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
    since: Annotated[
        str | None,
        typer.Option("--since", help="Only calls in this window: 90s, 30m, 12h, 7d, 2w."),
    ] = None,
) -> None:
    """What your agents already did: observed magnitudes, and the calls that exceeded a ceiling."""
    from neti.insight.report import build_report, format_report
    from neti.insight.window import parse_since, within
    from neti.store.jsonl import read_records

    try:
        rows = read_records(records)
        if since is not None:
            rows = within(rows, parse_since(since))
        summary = build_report(rows)
    except (OSError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    typer.echo(format_report(summary))


@app.command()
def propose(
    records: Annotated[str, typer.Option("--records", "-r")] = "out/decisions.ndjson",
    since: Annotated[
        str | None,
        typer.Option("--since", help="Only calls in this window: 90s, 30m, 12h, 7d, 2w."),
    ] = None,
) -> None:
    """Suggest ceilings from your own observed traffic, for a human to review and commit.

    Output is text to edit into a policy file. Nothing here is applied automatically, and nothing
    computed here is ever read at decision time.
    """
    from neti.insight.propose import format_proposals
    from neti.insight.propose import propose as build
    from neti.insight.report import build_report
    from neti.insight.window import parse_since, within
    from neti.store.jsonl import read_records

    try:
        rows = read_records(records)
        if since is not None:
            rows = within(rows, parse_since(since))
        summary = build_report(rows)
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
        uvicorn.run(
            create_app(state, serve_console=False), host=host, port=port, log_level="warning"
        )
    finally:
        state.close()


@app.command()
def console(
    config: Annotated[str, typer.Option("--config", "-c")] = "neti.yaml",
    records: Annotated[str, typer.Option("--records", "-r")] = "out/decisions.ndjson",
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8722,
    demo: Annotated[
        bool | None, typer.Option("--demo/--live", help="Force the fixture or a real tenant.")
    ] = None,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
) -> None:
    """Open the console: the API and the web UI, one process, one port.

    `neti serve` is the same API without the UI, for anyone running the web app from source.
    """
    try:
        import uvicorn

        from neti.api.app import create_app
        from neti.api.static import console_dir
    except ImportError as exc:
        typer.secho(
            "error: the console extra is not installed — run: pip install 'neti[console]'",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2) from exc

    from neti.api.state import build_state

    if console_dir() is None:
        # A source checkout that has never built the web app. Say what is missing and what to do,
        # rather than starting a server that answers every page with a 404.
        typer.secho("error: this install has no built console.", fg=typer.colors.RED, err=True)
        typer.echo(
            "\nFrom a source checkout:  cd web && npm install && npm run build && just console-sync"
            "\nOr run the API alone:     neti serve      (then cd web && npm run dev)",
            err=True,
        )
        raise typer.Exit(2)

    try:
        state = build_state(config=config, records=records, demo=demo)
    except (OSError, RuntimeError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    url = f"http://{host}:{port}"
    typer.secho(f"neti console on {url}", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  mode:    {state.mode}  ({state.tenant_label})")
    typer.echo(f"  policy:  {config}  digest {state.policy.digest()[:12]}")
    typer.echo(f"  records: {records}")

    if open_browser:
        import threading
        import webbrowser

        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    try:
        uvicorn.run(create_app(state), host=host, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
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
    config: Annotated[
        str | None,
        typer.Option("--config", "-c", help="Also replay every decision against this policy."),
    ] = None,
) -> None:
    """Verify the hash chain, and with `--config` re-derive every verdict from its evidence.

    Two different claims, and the second is the one the architecture exists for. The chain proves
    nothing has been altered since it was written. Replay proves each verdict *follows from the
    evidence in the record* — that these magnitudes and these declared ceilings still produce this
    answer under today's code. Upgrade `neti`, replay a year of log, and find out whether anything
    would now be decided differently.

    Replay needs the policy because a record stores the resolutions but not the ceilings they were
    compared against. Records written under a different policy are reported, not silently skipped.
    """
    from neti.core.record import verify_chain
    from neti.store.jsonl import read_records

    try:
        chain = list(read_records(records))
    except (OSError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    ok, bad = verify_chain(chain)
    if not ok:
        typer.secho(f"CHAIN BROKEN at decision {bad}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    typer.secho(f"{len(chain):,} records, chain intact", fg=typer.colors.GREEN)
    if chain:
        typer.echo(f"head: {chain[-1].record_digest}")

    if config is None:
        if chain:
            typer.echo("\nPass --config to also replay each decision against the policy.")
        return

    from neti.config.policy import PolicyError, load_policy
    from neti.insight.replay import format_replay, replay

    try:
        policy = load_policy(config)
    except (PolicyError, OSError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    result = replay(chain, policy)
    typer.echo("")
    typer.secho(
        format_replay(result),
        fg=typer.colors.GREEN if result.ok else typer.colors.RED,
        err=not result.ok,
    )
    if not result.ok:
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
