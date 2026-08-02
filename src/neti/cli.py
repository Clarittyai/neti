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

    The predicate moved onto `Policy` once `Preflight.from_config` turned out to need the same
    answer and not to have it. This stays as the name the CLI's four call sites already use.
    """
    return bool(policy.binds_entra())


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
            # Marks every record this run seals. `--demo` resolves against the synthetic tenant, and
            # its numbers are exact, confident and invented; the default records path is the same
            # one a real run uses.
            synthetic=demo,
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
        # Reading the chain head can fail on its own — an unreadable records path, a directory
        # where a file belongs — and that must not reach the handler below. It would exit 0 with
        # no stdout, which the hook protocol reads as *no opinion*: every gated call in the session
        # proceeding because a log file could not be opened. A chain that restarts is a visible
        # `neti verify` break; a gate that stopped gating is not visible at all.
        try:
            head = chain_head(records)
        except Exception as exc:
            print(
                f"neti hook: cannot read the record chain ({exc}); starting a new one",
                file=sys.stderr,
            )
            head = None
        engine = Engine(
            policy=policy,
            resolvers=resolvers,
            ctx=ResolveContext(timeout_ms=timeout_ms),
            last_digest=head,
            synthetic=demo,
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
    except Exception as exc:
        print(f"neti hook: {exc}", file=sys.stderr)
        raise typer.Exit(0) from exc
    finally:
        sink.close()
        client.close()

    # A decision that could not be filed still stands, and the operator has to hear about it. On
    # stderr because stdout is the decision protocol — anything else there corrupts it.
    if response and response.get("hookSpecificOutput", {}).get("neti", {}).get("record_error"):
        print(
            "neti hook: the decision was enforced but NOT recorded "
            f"({response['hookSpecificOutput']['neti']['record_error']}). "
            "The audit chain has a gap; `neti verify` will show it.",
            file=sys.stderr,
        )

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
    allow_synthetic: Annotated[
        bool,
        typer.Option(
            "--allow-synthetic",
            help="Propose from `--demo` traffic anyway. The numbers describe the built-in tenant.",
        ),
    ] = False,
) -> None:
    """Suggest ceilings from your own observed traffic, for a human to review and commit.

    Output is text to edit into a policy file. Nothing here is applied automatically, and nothing
    computed here is ever read at decision time.

    Refuses a window containing `--demo` decisions unless `--allow-synthetic` says otherwise: those
    magnitudes come from the built-in tenant, and a ceiling fitted to a fixture is worse than no
    ceiling because somebody will defend it.
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

    if summary.synthetic and not allow_synthetic:
        # Refused by default rather than warned. Everything this command prints is a number an
        # operator is about to paste into a policy and defend at 2am, and a ceiling fitted to the
        # built-in tenant is fitted to nothing. A warning above a table of confident figures gets
        # read as decoration; declining to print the table does not.
        #
        # Not refused outright, because `--demo` exists so the whole path can be walked with no
        # credentials and this is the last step of that walk. `--allow-synthetic` is the operator
        # saying they know which it is.
        typer.secho(
            f"error: {summary.synthetic:,} of {summary.decisions:,} decisions in this window are "
            "synthetic (`--demo`).",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Their magnitudes come from the built-in tenant, not from your traffic, so a\n"
            "ceiling proposed from them is fitted to a fixture. Point --records at a file only\n"
            "real traffic wrote, narrow the window with --since, or pass --allow-synthetic to\n"
            "see the shape of the answer anyway.",
            err=True,
        )
        raise typer.Exit(2)

    if summary.synthetic:
        typer.secho(
            f"⚠  {summary.synthetic:,} of {summary.decisions:,} decisions below are SYNTHETIC. "
            "These numbers describe the built-in tenant, not your agents.",
            fg=typer.colors.YELLOW,
        )
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
    field_results: Annotated[
        str,
        typer.Option(
            "--field",
            help="A field-trial result to fold in, from `eval/surveys/`. Absent is normal.",
        ),
    ] = "eval/results/mcp_coverage.json",
) -> None:
    """The scorecard: incident replay, friction, blind spots, and what is not yet measured.

    Runs entirely offline. Records and policy are optional — without them you still get the
    incident replay and the non-coverage list, which is most of what an audience asks about.

    Field-trial results are optional in the same way and for a stronger reason: M10 cannot be
    derived, only measured, so an installed wheel with no `eval/` directory prints it as outstanding
    rather than printing a number it made up. That is the rule the whole card runs on.
    """
    from neti.config.policy import PolicyError, load_policy
    from neti.eval.scorecard import Wild, build_scorecard, format_scorecard, scorecard_json
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

    wild = None
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        totals = json.loads(Path(field_results).read_text())["totals"]
        wild = Wild(
            servers_launched=int(totals["servers_launched"]),
            servers_in_catalogue=int(totals["servers_in_catalogue"]),
            tools_discovered=int(totals["tools_discovered"]),
            tools_gated=int(totals["tools_gated"]),
            tools_sizable_in_principle=int(totals["tools_sizable_in_principle"]),
        )

    card = build_scorecard(summary, policy, wild=wild)
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

    # This is the command an auditor runs, so it is the last place a synthetic row could pass for a
    # measured one. "Chain intact" is a statement about tampering and says nothing about provenance;
    # a reader who takes it as a blessing on the numbers has been misled by us.
    synthetic = sum(1 for record in chain if record.synthetic)
    if synthetic:
        typer.secho(
            f"\n⚠  {synthetic:,} of these {len(chain):,} records are SYNTHETIC (`--demo`): "
            "magnitudes from\n   the built-in tenant, not from any provider. The chain covers "
            "that marker, so it\n   is evidence of provenance and not a label anyone can strip.",
            fg=typer.colors.YELLOW,
        )

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
    here: Annotated[
        bool, typer.Option("--here", help="Measure THIS machine instead of the synthetic tenant.")
    ] = False,
    repo: Annotated[
        str | None, typer.Option("--repo", help="With --here: which directory. Default: cwd.")
    ] = None,
    corpus: Annotated[
        str | None,
        typer.Option("--corpus", help="With --here: captured traffic to re-run against it."),
    ] = None,
) -> None:
    """Run the whole narrative and emit it. `--here` runs it against your own machine.

    Without `--here`: the synthetic tenant. Every number is produced by the real decision path, so
    the demo cannot drift from the product — but the data is a fixture and the output says so. It
    demonstrates behaviour, not a finding.

    With `--here`: your files, your MCP configs, your token. The first two acts need nothing but the
    directory you are standing in, and they answer the question that actually matters on day one —
    how much can an agent here reach in one call. The rest needs traffic; with none, the demo says
    so and prints how to get it.
    """
    from neti.eval.demo import demo_json

    if here:
        _demo_here(config=config, repo=repo, corpus=corpus)
        return

    payload = demo_json(config)
    if out == "-":
        typer.echo(payload)
        return
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    typer.secho(f"wrote {path} ({len(payload):,} bytes)", fg=typer.colors.GREEN)


@app.command()
def install(
    config: Annotated[
        str, typer.Option("--config", "-c", help="Policy to gate with.")
    ] = "neti.yaml",
    user: Annotated[
        bool, typer.Option("--user", help="Gate every session, not just this project.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask.")] = False,
) -> None:
    """Wire the gate into Claude Code, without editing JSON by hand.

    Adds a `PreToolUse` hook to `.claude/settings.json` — merged into whatever is already there,
    idempotent, and backed up first. Prints the change and asks before writing, because this is a
    file you own and an agent depends on.

    Project-scoped by default: a policy's ceilings came from *this* repository's traffic and its
    `providers.fs.root` names *this* tree. `--user` gates every session on the machine.
    """
    from neti.config.policy import PolicyError, load_policy
    from neti.core.verdict import Mode
    from neti.engine import Engine
    from neti.insight.install import apply_install, plan_install
    from neti.resolvers.base import ResolverError

    policy = Path(config)
    if not policy.exists():
        typer.secho(f"error: no policy at {policy}", fg=typer.colors.RED, err=True)
        typer.echo(
            "\nWrite one first:\n"
            "  neti init                       from the MCP servers already on this machine\n"
            "  cp examples/coding-agent.yaml neti.yaml    for a coding agent",
            err=True,
        )
        raise typer.Exit(2)

    try:
        loaded = load_policy(policy)
        # Constructed, not merely parsed. `load_policy` is YAML validation; the four guards that
        # catch a misspelled resolver, an impossible breakdown band, a budget in an unproducible
        # unit and an unread provider key all run at `Engine.__post_init__`. A policy that parses
        # and cannot construct is the worst outcome to install: the hook catches the exception and
        # exits 0 on every call, so the session works perfectly and nothing is ever gated.
        resolvers, client = _build_resolvers(
            demo=False,
            timeout_ms=800,
            needs_entra=_needs_entra(loaded),
            providers=loaded.providers,
        )
        try:
            Engine(policy=loaded, resolvers=resolvers)
        finally:
            client.close()
    except (PolicyError, OSError, ValueError, ResolverError) as exc:
        typer.secho(f"error: {policy} does not load: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    try:
        plan = plan_install(Path.cwd(), policy.resolve(), user=user)
    except (ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        typer.echo("Refusing to overwrite settings that cannot be parsed.", err=True)
        raise typer.Exit(2) from exc

    if plan.already_installed:
        typer.secho(f"Already installed in {plan.path}", fg=typer.colors.GREEN)
        typer.echo(f"  {plan.command}")
        return

    typer.secho(f"Will write {plan.path}:", bold=True)
    typer.echo(f"\n{plan.diff()}\n")
    if plan.other_hooks:
        typer.echo(f"  ({plan.other_hooks} existing {plan.path.name} hook(s) left untouched)")
    if loaded.mode is Mode.OBSERVE:
        typer.secho(
            "  Policy is in observe mode: nothing will be blocked, everything recorded.",
            fg=typer.colors.BRIGHT_BLACK,
        )

    if not yes and not typer.confirm("Write it?", default=True):
        typer.echo("Nothing written.")
        raise typer.Exit(1)

    backup = apply_install(plan)
    typer.secho(f"Wrote {plan.path}", fg=typer.colors.GREEN, bold=True)
    if backup:
        typer.echo(f"  previous version saved as {backup.name}")
    typer.echo(
        "\nStart a new Claude Code session and work normally. Then:\n"
        "  neti report      what your agent actually touched\n"
        "  neti propose     ceilings from that traffic"
    )


@app.command()
def version() -> None:
    from neti import __version__

    typer.echo(__version__)


def _packaged_example(name: str) -> Path | None:
    """The shipped example, from a source checkout or from wherever the demo is being run.

    Tried in order rather than assumed, because `--here` is the command a stranger runs first and
    "file not found" is a worse first impression than any finding is a good one.
    """
    for candidate in (
        Path(__file__).resolve().parents[2] / "examples" / name,
        Path.cwd() / "examples" / name,
        Path.cwd() / name,
    ):
        if candidate.exists():
            return candidate
    return None


def _demo_here(*, config: str, repo: str | None, corpus: str | None) -> None:
    """Render `neti demo --here`.

    Every act is labelled with what it proves, because the difference between "measured on your
    machine" and "a captured session's shape, re-run against your files" is the difference between
    a finding and an anecdote, and a reader who cannot tell them apart should not trust either.
    """
    from neti.eval.corpus import Corpus, load_corpus
    from neti.eval.here import run_here

    root = Path(repo or ".").resolve()
    if not root.is_dir():
        typer.secho(f"error: {root} is not a directory", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)

    if config == "examples/entra.yaml":
        # `demo`'s default policy is the Entra example, which gates nothing a coding agent calls.
        # Under `--here` that produces a table of `?` — the demo measuring a directory nobody asked
        # about — so the default swaps to the policy this mode is built around. An explicit `-c`
        # still wins.
        found = _packaged_example("coding-agent.yaml")
        if found is None:
            typer.secho(
                "error: cannot find examples/coding-agent.yaml. Pass -c with your own policy.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(2)
        config = str(found)

    traffic: Corpus | None = None
    if corpus:
        try:
            traffic = load_corpus(Path(corpus))
        except (OSError, ValueError, KeyError) as exc:
            typer.secho(f"error: cannot read corpus {corpus}: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(2) from exc

    try:
        result = run_here(root, config, corpus=traffic)
    except (ValueError, OSError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    def rule(n: int, title: str, proves: str) -> None:
        typer.echo("")
        typer.secho(f"── {n}. {title} ".ljust(56, "─"), bold=True, nl=False)
        typer.secho(f"  {proves}", fg=typer.colors.BRIGHT_BLACK)

    typer.secho(f"neti demo — measured on {result.root}", bold=True)

    rule(1, "DISCOVER", "read from this machine")
    if result.servers:
        for server in result.servers:
            typer.echo(f"   {server['name']:22} {server['command']:40} ({server['client']})")
        typer.echo(f"   {len(result.servers)} MCP server(s) — none behind the gate")
    else:
        typer.echo("   no MCP servers configured on this machine")
    if result.already_gated:
        typer.echo(f"   already gated: {', '.join(result.already_gated)}")

    rule(2, "REACH", "MEASURED here, no traffic needed")
    # The whole stack, not just the layers this policy happens to gate. A table showing only what
    # `neti` covers invites the reader to assume everything absent is safe, which is backwards —
    # the layers with no resolver are the ones nothing is watching.
    from neti.eval.stack import State

    for row in result.stack:
        layer = row.layer
        if row.state is State.LISTENING and row.reach is not None:
            amount = f"{'\u2265 ' if row.bounded else ''}{row.reach:,}"
            typer.secho(
                f"   {'listening':11} {layer.name:16} {amount:>12} {layer.unit}",
                fg=typer.colors.GREEN,
            )
        elif row.state is State.LISTENING:
            typer.echo(f"   {'listening':11} {layer.name:16} {'—':>12} {row.note}")
        elif row.state is State.DARK:
            typer.secho(
                f"   {'dark':11} {layer.name:16} {'—':>12} {row.note}",
                fg=typer.colors.YELLOW,
            )
        else:
            typer.secho(
                f"   {'no resolver':11} {layer.name:16} {'—':>12} {layer.what}",
                fg=typer.colors.BRIGHT_BLACK,
            )

    typer.echo("")
    typer.echo(
        f"   {result.listening} layer(s) listening · {result.dark} dark for want of a credential · "
        f"{sum(1 for r in result.stack if r.state is State.UNCOVERED)} with no resolver at all"
    )

    for finding in result.findings[:1]:
        typer.echo("")
        typer.secho(f"   {finding.headline}", fg=typer.colors.YELLOW, bold=True)
        typer.echo(f"   {finding.detail}")

    if not result.has_traffic:
        typer.echo("")
        typer.secho("Acts 3-6 need traffic, and you have none yet.", bold=True)
        for line in result.next_steps:
            typer.echo(f"   {line}")
        typer.echo("")
        typer.secho(
            "Measured on this machine. Every number above was produced by walking these files, "
            "through the same decision path the gate uses in production.",
            fg=typer.colors.BRIGHT_BLACK,
        )
        return

    rule(3, "OBSERVE", "YOUR files, a captured session's shape")
    typer.echo(
        f"   {result.corpus_size:,} calls re-run · "
        f"{result.observed.get('allow', 0):,} allowed, nothing blocked (observe mode)"
    )
    if result.unresolved:
        typer.echo(
            f"   {result.unresolved:,} target(s) do not exist here — counted, not averaged away"
        )

    rule(4, "REPORT & PROPOSE", "from the traffic above")
    if not result.proposals:
        seen = result.report.distributions if result.report else {}
        typer.echo(
            f"   {len(seen)} parameter(s) observed, none with enough calls to propose a ceiling."
        )
        typer.echo(
            "   30 observations is the floor — a ceiling fitted to fewer encodes an accident."
        )
    for proposal in result.proposals[:3]:
        typer.echo(
            f"   {proposal.tool} {proposal.pointer}  n={proposal.n:,}  "
            f"{proposal.anchor}={proposal.normal:,}  max={proposal.observed_max:,}"
        )
        typer.echo(
            f"     proposed  confirm above {proposal.confirm_above:,}   "
            f"block above {proposal.block_above:,}"
        )

    rule(5, "ENFORCE", "the same calls, ceilings on")
    if not result.proposals:
        typer.echo("   nothing to enforce — no ceilings were proposed above")
    elif result.enforced:
        typer.echo(
            f"   blocked {result.enforced.get('block', 0):,} · "
            f"asked about {result.enforced.get('confirm', 0):,} · "
            f"allowed {result.enforced.get('allow', 0):,}"
        )
        if not result.enforced.get("block"):
            # A zero left hanging reads as "the gate did nothing". It is the same thing `propose`
            # says about a proposal that catches none of its own traffic, and it is a real result:
            # ceilings derived from ordinary work bind on behaviour that has not happened yet.
            typer.echo(
                "   Nothing in this traffic exceeded the block ceiling — these numbers came from "
                "it,\n   so they bind on what has not happened yet rather than on what has."
            )
    for example in result.blocked_examples:
        typer.secho(f"   BLOCKED  {example}", fg=typer.colors.RED)

    rule(6, "AUDIT", "offline, forever")
    typer.echo(
        f"   {result.records:,} records, chain {'intact' if result.chain_ok else 'BROKEN'} · "
        f"{result.replayed:,} decisions re-derive to the same verdict"
    )

    typer.echo("")
    typer.secho(result.disclaimer, fg=typer.colors.BRIGHT_BLACK)


def main() -> int:
    app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
