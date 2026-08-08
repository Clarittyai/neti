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


def missing_cli_extra(argv: list[str]) -> tuple[int, str]:
    """What to say, and what to exit with, when `typer` is not installed.

    `[project.scripts]` installs the `neti` command unconditionally, but `typer` lives in the `cli`
    extra — so `pip install neti` leaves a command that cannot start. It used to answer with a
    `ModuleNotFoundError` traceback, which is a poor first impression everywhere and a genuine
    hazard in one place:

    **`neti hook` exits 0 even here.** A `PreToolUse` hook that exits non-zero fails the tool call
    it was asked about, so an operator who hand-wrote the hook config on a base install would have
    every tool call in the session fail — not the gate silently off, which is bad, but the agent
    stopped dead, which is worse. The rule the rest of this file follows is that the gate never
    takes the session down with it, and an incomplete install is not an exception to it.

    Pure and returns rather than exits, so it can be tested without a subprocess and without
    uninstalling anything.
    """
    hint = (
        "neti: the command line needs the `cli` extra, which this install does not have.\n"
        "  pip install 'neti[cli]'      # or neti[all] for the console and Graph resolvers too\n"
        "The library itself is fine: `from neti import Preflight` works on a base install."
    )
    if len(argv) > 1 and argv[1] == "hook":
        return 0, (
            f"{hint}\n"
            "Exiting 0 because a PreToolUse hook that fails takes every tool call in the session "
            "with it. Nothing was gated for this call."
        )
    return 2, hint


def use_utf8_output() -> None:
    """Print UTF-8 whatever the platform thinks the console encoding is.

    Every screen this CLI draws uses characters above ASCII: the `──` rules in `neti demo`, the `·`
    separators, the `≥` that says a walk stopped at its cap, the em-dashes throughout. On macOS and
    Linux stdout is UTF-8 and none of it is a question. On Windows it is cp1252, which can encode
    none of them, so `neti demo --here` died with a `UnicodeEncodeError` traceback instead of
    printing anything, and so did `report`, `propose`, `verify` and `prove`.

    **`neti hook` is why this matters most.** The hook writes JSON containing the denial
    sentence, which carries an em-dash: on Windows it would raise, exit non-zero, and a
    `PreToolUse` hook that exits non-zero *fails the tool call it was asked about*. Every gated
    call in the session would die. This file's rule is that the gate never takes the session
    down with it, and an encoding it did not choose is not an exception to that.

    Called from `main`, which `[project.scripts]` now points at, rather than at import. An
    import-time version reconfigures the stream of anything that merely imports this module,
    including pytest's capture, and it showed up as a cold-start cost in the latency bench.

    `errors="replace"` rather than strict: a console that still cannot render a box-drawing
    character should show a placeholder, not take the command down.

    Guarded on `reconfigure`, because stdout is not always a real stream — `CliRunner` hands it a
    `StringIO`, which has no such method and needs no such fix.

    The suite could not see any of this until the repository was first pushed and a Windows job ran
    for the first time. `neti init` carries a Windows branch for finding Claude Desktop's config, so
    it is plainly a platform we expect to be on.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(OSError, ValueError):
            reconfigure(encoding="utf-8", errors="replace")


try:
    import typer
except ModuleNotFoundError:  # pragma: no cover - exercised by test_the_cli_without_its_extra
    _code, _message = missing_cli_extra(sys.argv)
    print(_message, file=sys.stderr)
    raise SystemExit(_code) from None

app = typer.Typer(
    add_completion=False,
    help="A preflight gate for agent tool calls: resolve what an action will touch, "
    "block it if it exceeds a declared ceiling.\n\n"
    "New here? Run `neti start`.",
    # A person meeting this tool used to be shown nineteen commands in one flat list, with
    # `measure` — an internal Graph-latency benchmark — second. The five evaluation commands
    # (`measure`, `check`, `score`, `prove`, `serve`) are hidden rather than removed: they still
    # run, so every doc and golden transcript that names them still works, but they are not part of
    # anybody's first screen.
    no_args_is_help=False,
)


@app.command(rich_help_panel="Start here")
def start(
    config: Annotated[
        str, typer.Option("--config", "-c", help="Where to write the policy.")
    ] = "neti.yaml",
) -> None:
    """New here? Run this. It sets neti up and shows you what your agent can reach.

    The first run used to be nineteen commands in a flat list and a policy file asking for a
    ceiling — a number nobody has on day one. This is the path instead: find the agent, write a
    policy, protect the obvious, and measure *this* machine so the first thing you see is a fact
    about your own repository rather than an example about somebody else's.

    Nothing here enforces anything. That is deliberate: you cannot pick a ceiling before you have
    seen your own numbers, so day one is measurement and day two is the ceiling.
    """
    from neti.config.policy import PolicyError, load_policy
    from neti.insight.discover import find_clients
    from neti.insight.inventory import build_inventory
    from neti.resolvers.base import ResolveContext, ResolverError

    here = Path.cwd()
    typer.secho("\nneti — first run", bold=True)
    typer.echo(f"  in {here}\n")

    # ---------------------------------------------------------------- 1. what is on this machine
    typer.secho("1. Looking for an agent", bold=True)
    try:
        servers = find_clients(cwd=here)
    except Exception:  # discovery must never be the thing that stops a first run
        servers = []
    if servers:
        names = ", ".join(sorted({s.name for s in servers})[:6])
        typer.echo(f"   {len(servers)} MCP server(s) configured here: {names}")
    else:
        typer.echo("   No MCP servers configured here — that is fine, and common.")
    typer.echo("   Claude Code's own built-in tools are gated through a hook, not MCP.\n")

    # ---------------------------------------------------------------- 2. a policy that blocks
    typer.secho("2. Writing a policy", bold=True)
    destination = Path(config)
    if destination.exists():
        typer.echo(f"   {destination} already exists, keeping it.")
    else:
        _write_example("coding-agent", destination, quiet=True)
        typer.echo(f"   wrote {destination}. You can read the whole thing, and step 4 fills in")
        typer.echo("   what it starts out protecting.")
    typer.echo("")

    # ---------------------------------------------------------------- 3. the number
    typer.secho("3. Measuring this machine", bold=True)
    reached: int | None = None
    try:
        policy = load_policy(str(destination))
        providers = {
            **policy.providers,
            "fs": {**(policy.providers.get("fs") or {}), "root": str(here)},
        }
        policy = policy.model_copy(update={"providers": providers})
        resolvers, client = _build_resolvers(
            demo=True, timeout_ms=4000, needs_entra=False, providers=policy.providers
        )
        try:
            rows = build_inventory(policy, resolvers, ResolveContext(timeout_ms=4000))
        finally:
            if client is not None:
                client.close()
        # `reachable` is a Resolution, and its magnitude is None unless it actually resolved —
        # that validator is the reason a directory we could not read never reads as zero here.
        sizes = [
            r.reachable.magnitude
            for r in rows
            if r.reachable is not None and r.reachable.magnitude is not None
        ]
        reached = max(sizes) if sizes else None
    except (PolicyError, OSError, ResolverError, ValueError):
        reached = None

    if reached is not None:
        typer.echo("   The largest set one gated call could touch, right now, here:\n")
        typer.secho(f"      {reached:,} objects", bold=True, fg=typer.colors.CYAN)
        typer.echo("\n   That is capability, not an incident. Nothing has gone wrong — it is the")
        typer.echo("   answer to a question nothing else in your stack asks.")
    else:
        typer.echo("   Could not measure from here yet. `neti inventory` will say why.")
    typer.echo("")

    # ---------------------------------------------------------------- 4. turn it on
    #
    # **This used to be homework.** A fresh install had nine gated parameters, zero ceilings and
    # `mode: observe` — it protected nothing and could not, until somebody opened a 210-line YAML
    # file and learned fourteen keys. Eight steps and a week stood between `pip install` and any
    # protection at all, and the median install never finished them.
    #
    # The founding principle — you cannot pick a ceiling before you have seen your own numbers — is
    # right about a *tuned* ceiling and wrong about a catastrophic one. Nobody's normal workflow
    # deletes twenty thousand files or reads `~/.ssh`.
    #
    # The line that keeps it honest is in `insight/preset.py`: **day zero never blocks on a number
    # we chose.** Sizes only flag. The two things that stop a call are an identity match on a file
    # named in the output below, and a target outside this directory — what a thing is, and where
    # it is. Neither is a number.
    from neti.insight.edit_policy import PolicyEditError, apply_preset, plan_preset
    from neti.insight.preset import build as build_preset

    preset = build_preset(here, reached or 0)
    applied = False
    if preset.protects_anything:
        typer.secho("4. Turning it on", bold=True)
        try:
            edit = plan_preset(
                destination,
                bands=[{"above": preset.flag_above, "verdict": "flag"}] if reached else [],
                rules=[
                    {"match": c.match, "verdict": c.verdict, "why": c.why}
                    for c in preset.off_limits
                ],
                session_above=preset.session_above if reached else 0,
                outside_root="confirm",
            )
            apply_preset(edit)
            applied = True
        except (PolicyEditError, OSError) as exc:
            typer.secho(f"   could not write the starting rules: {exc}", fg=typer.colors.YELLOW)

    if applied:
        if preset.off_limits:
            typer.echo("   Off limits from now on — these are really here, and one file is")
            typer.echo("   under every ceiling anybody would write:\n")
            for c in preset.off_limits[:4]:
                typer.echo(f"      {c.match:<16} {c.why}   (found {c.example})")
            typer.echo("")
        if reached:
            typer.echo(f"   And anything touching more than {preset.flag_above:,} at once is")
            typer.echo("   flagged — recorded, and you get told. Never stopped: a size threshold")
            typer.echo("   is our judgement, and we do not stop your work on one.\n")
            typer.echo(f"   Same for a whole session adding up past {preset.session_above:,} —")
            typer.echo("   two hundred single-file edits is how a codebase gets rewritten by")
            typer.echo("   accident, and no per-call ceiling can ever see it.\n")
        typer.echo("   And anything outside this directory — your home folder, /etc, a sibling")
        typer.echo("   project — asks first. Your agent's keys mostly live there, not here.\n")
        typer.echo(f"   All of it is in {destination}, and changeable in `neti console`.")
        typer.echo("")

    # ---------------------------------------------------------------- 5. in front of the agent
    typer.secho("5. Putting it in front of your agent", bold=True)
    wired = _offer_install(here, destination)
    typer.echo("")

    # ---------------------------------------------------------------- 6. watch it work, now
    #
    # The gap this closes: after the install, *nothing visibly happens*. Silence is the correct
    # behaviour for the agent and the wrong experience for the person who just installed a security
    # tool — they have no way to tell it is alive until an agent happens to do something large,
    # which could be days. So fire one real call through the real engine, against one of their own
    # files, and show them the answer.
    if applied and preset.off_limits:
        typer.secho("6. Here it is working", bold=True)
        _show_one_call(destination, here, preset.off_limits[0].example)
        typer.echo("")

    # ---------------------------------------------------------------- 7. what happens next
    typer.secho("7. From here", bold=True)
    if wired:
        typer.echo("   Working. Go build — you will hear from it if something looks big.")
    else:
        typer.echo("   Nothing is gated until the hook is wired:  neti install")
    typer.echo("")
    typer.echo("   neti console         everything above, in a browser, with your own numbers")
    typer.echo("   neti report          what your agent actually touched")
    typer.echo("   neti propose         tighter ceilings, from a week of your own traffic")
    typer.echo("")
    typer.echo("   The ceilings above are starting points chosen by us, not measurements of you.")
    typer.echo("   `neti propose` replaces them with numbers from your own work.\n")


def _show_one_call(policy_path: Path, here: Path, target: str) -> None:
    """Drive one real call through the real engine and print what came back.

    Not a simulation and not a recording: the same `Engine`, the same policy just written, the same
    resolver. It records nothing — no sink — because a decision the operator did not make should not
    land in the chain they are about to start reading.
    """
    from neti.config.policy import load_policy
    from neti.core.types import ProposedCall
    from neti.engine import Engine
    from neti.resolvers.base import ResolverError

    try:
        policy = load_policy(policy_path)
        resolvers, client = _build_resolvers(
            demo=False, timeout_ms=2000, needs_entra=False, providers=policy.providers
        )
        try:
            engine = Engine(policy=policy, resolvers=resolvers)
            result = engine.gate(ProposedCall(tool="Read", args={"file_path": target}))
        finally:
            client.close()
    except (OSError, ValueError, ResolverError):
        return

    from neti.gateway.mcp import explain_denial

    typer.echo(f"   Your agent asks to read {target}:\n")
    sentence = "" if result.proceeds else explain_denial(result, engine.denial_payload(result))
    if sentence:
        typer.secho(f"      {sentence}", fg=typer.colors.YELLOW)
        typer.echo("\n   That is the gate. It did not run, and nothing was recorded for this one —")
        typer.echo("   it is a demonstration, not a decision you made.")
    else:
        typer.echo(f"      allowed — {result.decision.args[0].resolution.magnitude} object(s)")


def _offer_install(here: Path, policy: Path) -> bool:
    """Wire the hook, showing the change and asking once.

    Collapsed into `neti start` because it was the step most likely to be dropped and nothing works
    until it happens — a first run that ends by telling you to run a second command is a first run
    with a cliff in the middle. The safety is unchanged: `plan_install` still shows the diff, still
    refuses settings it cannot parse, and `apply_install` still backs the file up.
    """
    from neti.insight.install import apply_install, plan_install

    try:
        plan = plan_install(here, policy.resolve())
    except (ValueError, json.JSONDecodeError) as exc:
        typer.secho(f"   {exc}", fg=typer.colors.YELLOW)
        typer.echo("   Refusing to touch settings it cannot parse. `neti install` once fixed.")
        return False

    if plan.already_installed:
        typer.echo(f"   Already wired into {plan.path}")
        return True

    typer.echo(f"   This adds a PreToolUse hook to {plan.path}:\n")
    typer.echo(f"{plan.diff()}\n")
    if plan.other_hooks:
        typer.echo(f"   ({plan.other_hooks} existing hook(s) left untouched)")

    # Nobody to ask is not the same as consent. A `neti start` in CI, or piped, must not write to a
    # file the operator owns on the strength of a default — and it must not hang waiting for a
    # keystroke that cannot arrive, which is what `typer.confirm` does with no tty.
    if not sys.stdin.isatty():
        typer.echo("   Not a terminal, so nothing was written. Run `neti install` when you are.")
        return False

    if not typer.confirm("   Write it?", default=True):
        typer.echo("   Nothing written. `neti install` when you are ready.")
        return False

    backup = apply_install(plan)
    typer.secho(f"   Wrote {plan.path}", fg=typer.colors.GREEN)
    if backup:
        typer.echo(f"   previous version saved as {backup.name}")
    return True


@app.command(rich_help_panel="Start here")
def init(
    out: Annotated[
        str, typer.Option("--out", "-o", help="Where to write the policy.")
    ] = "neti.yaml",
    probe: Annotated[
        bool,
        typer.Option(help="Launch each server to ask what tools it exposes. --no-probe to skip."),
    ] = True,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing policy.")] = False,
    example: Annotated[
        str | None,
        typer.Option(
            "--example",
            help="Start from a shipped policy instead of discovery: coding-agent, entra, "
            "data-agent, infra-agent.",
        ),
    ] = None,
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

    if example is not None:
        _write_example(example, target)
        return

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
            "Claude Desktop config. Plenty of agents have no MCP server at all, and they can\n"
            "still be gated:\n\n"
            "  neti init --example coding-agent    Claude Code's built-in Read, Edit, Glob, Bash\n"
            "  neti hook --help                    how that reaches the agent\n"
            "  from neti import Preflight          a tool loop you wrote yourself"
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
    target.write_text(render_policy(found), encoding="utf-8")

    typer.secho(f"\nWrote {out}", bold=True)
    first_server = found.servers[0] if found.servers else None
    wrap = " ".join(first_server.argv) if first_server else "<your server command>"

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


@app.command(hidden=True)
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


@app.command(hidden=True)
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


@app.command(rich_help_panel="Every day")
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
        policy = load_policy(resolve_policy(config))
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


@app.command(rich_help_panel="When you need it")
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


@app.command(rich_help_panel="When you need it")
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
    from neti.resolvers.graph_client import ClientCredential, GraphClient
    from neti.resolvers.registry import build_entra_resolvers, resolvers_for_client

    # `neti.eval.synthetic` is the fixture tenant and it is built on an httpx MockTransport, so
    # importing it here unconditionally made every non-demo command need the `graph` extra —
    # `neti hook` on a filesystem policy included, which is the cheapest install this product has.
    # The same shape as the httpx import in `graph_client`, one level up: a path paying for a
    # dependency belonging to a path it never takes.
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

    from neti.eval.synthetic import default_tenant

    credential = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")
    client = GraphClient(credential, transport=default_tenant().transport(), timeout_ms=timeout_ms)
    return resolvers_for_client(client, providers), client


@app.command(
    rich_help_panel="When you need it",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
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
        policy = _apply_mode(load_policy(resolve_policy(config)), mode_override)
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


def _approver(org: bool, *, fatal: bool = True) -> Any:
    """A control plane, if the operator asked for one and is logged in.

    Refuses rather than falling back silently: someone who passed `--org` believes their `CONFIRM`s
    reach a human, and quietly running without one would leave them thinking approvals are wired up
    when every such call is simply being stopped.

    `fatal=False` for the hook, and only for the hook. `neti gate` is a long-lived process and
    exiting 2 at startup is the right, loud answer; `neti hook` is one process per tool call, and a
    non-zero exit there fails the tool call it was asked about — so a missing login would take the
    whole session down rather than one call. It says the same thing and carries on, which leaves a
    `CONFIRM` stopping the call: the free tier's behaviour, and the one the paid tier degrades to.
    """
    if not org:
        return None

    from neti.cloud import HttpApprover, load_credentials

    creds = load_credentials()
    if creds is None or not creds.configured:
        if not fatal:
            print(
                "neti hook: --org needs a control plane; run `neti login` first. Continuing "
                "without one, so a CONFIRM stops the call rather than reaching a human.",
                file=sys.stderr,
            )
            return None
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


@app.command(rich_help_panel="When you need it")
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
    org: Annotated[
        bool,
        typer.Option("--org", help="Escalate a CONFIRM to your control plane. Needs `neti login`."),
    ] = False,
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
    from neti.store.sessions import SessionStore

    try:
        event = read_event(sys.stdin.read())
        # `resolve_policy(..., fatal=False)` returns rather than exiting. A `PreToolUse` hook
        # that exits non-zero fails the tool call it was asked about, so the guidance every
        # other command prints has to arrive here without the exit that carries it.
        policy = _apply_mode(load_policy(resolve_policy(config, fatal=False)), mode_override)
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
            # One process per tool call, so the in-memory tally is empty every time and a declared
            # session budget could never fire here — which made `SCOPE.md` NC-01's "mitigated only
            # by declared session budgets" false on the integration most people use.
            sessions=SessionStore(records),
        )
    except Exception as exc:
        # Deliberately every exception. A hook that cannot run must not take the session down with
        # it: say why on stderr, exit 0, and let the call proceed under whatever rules were already
        # in place. Failing closed here would block every tool the moment a credential expired.
        print(f"neti hook: {exc}", file=sys.stderr)
        raise typer.Exit(0) from exc
    sink = JsonlSink(records)
    try:
        # `fatal=False`: a hook that exits non-zero fails the tool call it was asked about, so a
        # missing `neti login` must degrade to the free tier rather than take the session down.
        response = run_hook(engine, event, sink, _approver(org, fatal=False))
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


@app.command(rich_help_panel="Every day")
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
        rows = read_records(resolve_records(records, what="`neti report`"))
        if since is not None:
            rows = within(rows, parse_since(since))
        summary = build_report(rows)
    except (OSError, ValueError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    typer.echo(format_report(summary))


@app.command(rich_help_panel="When you need it")
def suggest(
    out: Annotated[
        str, typer.Option("--out", "-o", help="Where to write the fragment.")
    ] = "neti.suggested.yaml",
    provider: Annotated[
        str,
        typer.Option(
            "--provider",
            help="anthropic, openai, or local. Hosted uses your key and your account; "
            "local sends nothing off this machine.",
        ),
    ] = "anthropic",
    model: Annotated[str | None, typer.Option("--model")] = None,
    base_url: Annotated[
        str | None,
        typer.Option(
            "--base-url",
            help="For --provider local. Any OpenAI-compatible runner; defaults to Ollama.",
        ),
    ] = None,
    timeout: Annotated[
        float | None,
        typer.Option(
            "--timeout",
            help="Seconds per request. A large local model loading from cold needs minutes.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print exactly what would be sent, and send nothing."),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before sending.")] = False,
    batch_size: Annotated[int, typer.Option("--batch-size")] = 8,
) -> None:
    """Ask YOUR model which unclaimed parameters name a set. Suggestions only, never a policy.

    `neti init` gates what its rule table can claim and leaves the rest listed as "no resolver
    claims this". This asks a model about that remainder, using your own key from your own machine:
    neti never proxies the request and never sees the answer.

    What comes back is a commented-out YAML fragment in a separate file. Nothing is active until you
    delete the `#` yourself, and even then every band is empty, so a merged suggestion resolves and
    records but cannot block. `neti suggest` never edits your policy.
    """
    from neti.insight.assist import (
        SYSTEM,
        Suggestion,
        batches,
        eligible,
        parse,
        question,
        render_fragment,
        schema,
    )
    from neti.insight.discover import discover, find_clients

    target = Path(out)
    policy = Path("neti.yaml")
    if target.resolve() == policy.resolve():
        typer.secho(
            "error: --out must not be your policy. This writes suggestions, not configuration.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    servers = find_clients(already_gated=[])
    if not servers:
        typer.secho("No MCP servers found, so there is nothing to ask about.", fg="yellow")
        typer.echo("`neti suggest` reads the same servers `neti init` does.")
        raise typer.Exit(1)

    found = discover(servers, probe=True)
    candidates = eligible(list(found.tools))
    if not candidates:
        typer.secho("Every parameter here is already claimed or already judged.", fg="green")
        typer.echo("Nothing to ask a model about.")
        return

    groups = batches(candidates, size=batch_size)
    tools = len({c.tool for c in candidates})

    typer.secho("neti suggest — asks YOUR model which unclaimed parameters name a set.", bold=True)
    typer.echo(
        f"\n  {tools} tool(s) carry {len(candidates)} parameter(s) the rule table did not claim.\n"
        f"  {len(groups)} request(s) to {_destination(provider, base_url)}.\n"
        "\n  What is sent: tool names, parameter names, sibling names, and the first line of each\n"
        "  description. Nothing else: not your policy, not your ceilings, not your records,\n"
        "  not the server commands or their environment. --dry-run prints it."
    )

    if dry_run:
        typer.echo("\n--- system prompt ---")
        typer.echo(SYSTEM)
        for index, group in enumerate(groups, start=1):
            typer.echo(f"--- request {index} of {len(groups)} ---")
            typer.echo(question(group, indent=2))
        typer.echo("--- nothing was sent ---")
        return

    if not yes and not typer.confirm("\nSend it?", default=False):
        typer.echo("Nothing sent.")
        raise typer.Exit(1)

    from neti.insight.assist_client import Refused, client_for

    try:
        client = client_for(provider, model, base_url=base_url)
        if timeout and hasattr(client, "timeout_s"):
            client.timeout_s = timeout
    except ValueError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None

    suggestions: list[Suggestion] = []
    unsized: list[Suggestion] = []
    rejected: list[Any] = []
    unassisted = 0

    with typer.progressbar(groups, label="asking") as progress:
        for group in progress:
            try:
                answer = client.ask(SYSTEM, question(group), schema())
            except ModuleNotFoundError:
                typer.secho(
                    f"\nerror: the {provider} SDK is not installed.\n"
                    f"  pip install 'neti[assist]'          for anthropic\n"
                    f"  pip install 'neti[assist-openai]'   for openai",
                    fg=typer.colors.RED,
                    err=True,
                )
                raise typer.Exit(2) from None
            except Refused as exc:
                unassisted += 1
                rejected.append(("refused", f"{exc}"))
                continue
            except Exception as exc:  # a provider error must not lose the batches that worked
                unassisted += 1
                rejected.append((type(exc).__name__, str(exc)[:160]))
                continue

            got, bad = parse(answer.text, group)
            suggestions.extend(got)
            rejected.extend((r.reason, r.detail) for r in bad)

    fragment = render_fragment(
        suggestions, model=client.name, provider=client.provider, unsized=unsized
    )
    target.write_text(fragment, encoding="utf-8")

    typer.echo("")
    for s in sorted(suggestions, key=lambda x: (x.tool, x.parameter)):
        typer.secho(f"  {s.tool}  /{s.parameter}", bold=True)
        typer.echo(f"      {s.resolver}   UNVERIFIED")
        typer.echo(f"      {s.why}")
    typer.echo(
        f"\n  {len(candidates)} parameter(s) looked at · {len(suggestions)} claimed · "
        f"{len(rejected)} discarded"
    )
    if unassisted:
        typer.secho(f"  {unassisted} request(s) failed and were not merged.", fg="yellow")
    typer.secho(f"\nWrote {target}. Every block in it is commented out.", bold=True)
    typer.echo("Nothing is active until you delete the `#` in front of it yourself.")


@app.command(rich_help_panel="Every day")
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
        rows = read_records(resolve_records(records, what="`neti propose`"))
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

    # The other axis, and the reason it is here rather than in its own command: this is already
    # the place somebody comes to ask *what should I declare*. `sensitive:` shipped commented out
    # in the example policy and mentioned in a changelog, which is the same as not shipping it —
    # a capability nobody can find is a capability nobody has.
    #
    # Only rules that match something really on this disk. Offering `**/*.pem` to a repository with
    # no certificate in it is offering a rule that can never fire, which is the dead-config failure
    # every other part of this file is careful about.
    from neti.insight.secrets_scan import render, scan

    try:
        found = render(scan(Path.cwd()))
    except OSError:
        found = ""
    if found:
        typer.echo("\n" + "─" * 78 + "\n")
        typer.echo(found)


@app.command(hidden=True)
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
        state = build_state(config=str(resolve_policy(config)), records=records, demo=demo)
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


@app.command(rich_help_panel="Every day")
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
            "\nThe console is a web app built at package time. An install from source (a git\n"
            "checkout, or `pip install` from a repository URL) does not carry one; the published\n"
            "package does.\n\n"
            "  neti serve                     the same API with no UI, on this machine\n"
            "  neti report                    the same numbers, in the terminal\n"
            "\nTo build it here:  cd web && npm install && npm run build && just console-sync",
            err=True,
        )
        raise typer.Exit(2)

    try:
        state = build_state(config=str(resolve_policy(config)), records=records, demo=demo)
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


@app.command(hidden=True)
def prove(
    # Your policy, not the shipped Entra example. It defaulted to `examples/entra.yaml`, so
    # somebody running `neti prove` inside their own project got a wall of output about 41,203
    # principals in a synthetic tenant — a command whose banner reads "EVERY DOOR ON THIS MACHINE"
    # proving something about a fixture, on a machine with none of it. The error path below already
    # explains the fixture case properly; it just was not reachable.
    config: Annotated[str, typer.Option("--config", "-c")] = "neti.yaml",
    records: Annotated[
        str, typer.Option("--records", "-r", help="Where to write the chain it produces.")
    ] = "out/proof.ndjson",
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable output.")] = False,
) -> None:
    """Run one call through every door installed here, and seal the evidence.

    Eleven adapters is a number in a README. This drives the same call through each seam this
    machine can actually reach, prints the verdict, the magnitude and the sentence each one
    produced, and verifies the hash chain they wrote — which you can re-check yourself with
    `neti verify -r` against the same file.

    A seam whose SDK is not installed is reported as *not driven here*, naming the test that does
    drive it. It is never shown as though it had been measured.
    """
    from neti.eval.proof import format_proof, run_proof

    path = Path(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = resolve_policy(config)

    # The call comes from the policy in hand — see `pick_call`. `prove` used to drive one fixed
    # Entra call, so the default config was the one config it refused: after `neti start`, the
    # command that `start`, the demo and the README all point at ended in an error naming itself as
    # the thing to run instead. It only stops here when nothing in the policy can be driven at all.
    from neti.config.policy import load_policy
    from neti.eval.proof import pick_call

    if pick_call(load_policy(resolved)) is None:
        typer.secho(
            f"error: nothing in {resolved} can be driven by `neti prove`.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.echo(
            "\n`neti prove` drives one call your policy STOPS through every door installed\n"
            "here, and checks they all reach the same verdict. It proves the seams agree; it\n"
            "does not read your traffic or your numbers.\n\n"
            "This policy gates nothing it can use: no `fs.paths` parameter with either an\n"
            "off-limits rule or `outside_root:` to stop it. Either is one line —\n"
            "`neti start` writes both — or:\n\n"
            # Not column-aligned against the config path: it is arbitrary length, and a second
            # column that lines up for `neti.yaml` and not for anything else looks broken.
            "  neti prove -c examples/entra.yaml\n"
            "      the shipped example, driven against the built-in fixture\n"
            f"  neti demo --here -c {config}\n"
            "      what YOUR policy reaches, measured here",
            err=True,
        )
        raise typer.Exit(2)

    proof = run_proof(str(resolved), path)

    if as_json:
        from dataclasses import asdict

        typer.echo(json.dumps({**asdict(proof), "records_path": str(path)}, indent=2, default=str))
    else:
        typer.echo(format_proof(proof))

    # A disagreement between doors is a defect in the product, not a note in a demo.
    if not proof.chain_ok or not proof.agreed:
        raise typer.Exit(1)


@app.command(hidden=True)
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
    live_results: Annotated[
        str,
        typer.Option(
            "--live",
            help="What the live tier last proved, from `just live`. Absent is normal.",
        ),
    ] = "eval/results/live_verification.json",
    assist_results: Annotated[
        str,
        typer.Option(
            "--assist",
            help="What `just assist` last measured about model-assisted suggestion. "
            "Absent is normal.",
        ),
    ] = "eval/results/assist_recovery.json",
    conformance_results: Annotated[
        str,
        typer.Option(
            "--conformance",
            help="What `just conformance` last proved about each agent runtime. Absent is normal.",
        ),
    ] = "eval/results/conformance.json",
) -> None:
    """The scorecard: incident replay, friction, blind spots, and what is not yet measured.

    Runs entirely offline. Records and policy are optional — without them you still get the
    incident replay and the non-coverage list, which is most of what an audience asks about.

    Field-trial results are optional in the same way and for a stronger reason: M10 cannot be
    derived, only measured, so an installed wheel with no `eval/` directory prints it as outstanding
    rather than printing a number it made up. That is the rule the whole card runs on.
    """
    from neti.config.policy import PolicyError, load_policy
    from neti.eval.scorecard import Assist, Wild, build_scorecard, format_scorecard, scorecard_json
    from neti.insight.report import build_report
    from neti.store.jsonl import read_records

    # No traffic and no policy are normal states here, not errors: the incident replay and the
    # blind-spot list are useful before anything is configured, which is most of the point.
    summary = None
    with contextlib.suppress(OSError, ValueError):
        summary = build_report(read_records(resolve_records(records, what="`neti score`")))

    policy = None
    with contextlib.suppress(PolicyError, OSError):
        policy = load_policy(config)

    wild = None
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        totals = json.loads(Path(field_results).read_text(encoding="utf-8"))["totals"]
        wild = Wild(
            servers_launched=int(totals["servers_launched"]),
            servers_in_catalogue=int(totals["servers_in_catalogue"]),
            tools_discovered=int(totals["tools_discovered"]),
            tools_gated=int(totals["tools_gated"]),
            tools_sizable_in_principle=int(totals["tools_sizable_in_principle"]),
        )

    # Same rule as `--field`: absent means "not run here", which the card prints as such rather
    # than assuming the claim holds. A resolver `LIVE_VERIFIED` asserts but no run confirms is a
    # third state, and it is the one worth seeing.
    live: dict[str, Any] | None = None
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        live = json.loads(Path(live_results).read_text(encoding="utf-8"))["resolvers"]

    # Same rule again. M12 cannot be derived: it needs somebody's key, and an absent file means
    # nobody has run it here rather than that a model did badly.
    assist = None
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        raw = json.loads(Path(assist_results).read_text(encoding="utf-8"))
        # Every arm is optional: `--arm unclaimed` produces a file with no recovery in it, and a
        # card that vanished because one arm was not run would be reporting on the harness rather
        # than on the model.
        recovery = raw.get("recovery") or {}
        over = raw.get("over_claim") or {}
        unclaimed = raw.get("unclaimed") or {}
        if not (recovery or over or unclaimed):
            raise ValueError("no arm was run")
        assist = Assist(
            model=str(raw.get("model", "")),
            provider=str(raw.get("provider", "")),
            of=int(recovery.get("of", 0)),
            recovered=int(recovery.get("recovered", 0)),
            wrong_resolver=int(recovery.get("wrong_resolver", 0)),
            missed=int(recovery.get("missed", 0)),
            extra=int(recovery.get("extra", 0)),
            contested=int(over.get("of", 0)),
            over_claimed=int(over.get("over_claimed", 0)),
            unclaimed_of=int(unclaimed.get("of", 0)),
            unclaimed_found=int(unclaimed.get("found", 0)),
            unclaimed_false=int(unclaimed.get("false_claim", 0)),
            unclaimed_forced=int(unclaimed.get("forced_near_miss", 0)),
            unclaimed_unadjudicated=int(unclaimed.get("unadjudicated", 0)),
        )

    # Same rule as M10, M11 and M12: an absent file means nobody ran it here, never that a
    # runtime failed. `just conformance` produces it and needs no key at all.
    conformance: dict[str, dict[str, Any]] | None = None
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        conformance = json.loads(Path(conformance_results).read_text(encoding="utf-8"))["runtimes"]

    conformance_live: dict[str, dict[str, Any]] | None = None
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        conformance_live = json.loads(
            Path(conformance_results).with_name("conformance_live.json").read_text(encoding="utf-8")
        )["runtimes"]

    card = build_scorecard(
        summary,
        policy,
        wild=wild,
        live=live,
        assist=assist,
        conformance=conformance,
        conformance_live=conformance_live,
    )
    typer.echo(scorecard_json(card) if as_json else format_scorecard(card))


@app.command(rich_help_panel="When you need it")
def verify(
    records: Annotated[str, typer.Option("--records", "-r")] = "out/decisions.ndjson",
    config: Annotated[
        str | None,
        typer.Option("--config", "-c", help="Also replay every decision against this policy."),
    ] = None,
    mode_override: Annotated[
        str | None,
        typer.Option("--mode", help="Replay as if the policy were in this mode: observe|enforce."),
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

    `--mode` exists because observe and enforce are *different policies* with different digests, so
    records written while enforcing do not replay against the same file read as observe. That is
    correct and it is not obvious, and without a way to say which mode produced them the honest
    "decided under a different policy" line is a dead end rather than an instruction.
    """
    from neti.core.record import verify_chain
    from neti.store.jsonl import read_records

    try:
        chain = list(read_records(resolve_records(records, what="`neti verify`")))
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
        policy = _apply_mode(load_policy(resolve_policy(config)), mode_override)
    except (PolicyError, OSError, KeyError) as exc:
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


@app.command(rich_help_panel="Start here")
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

    payload = demo_json(str(resolve_policy(config)))
    if out == "-":
        typer.echo(payload)
        return
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    typer.secho(f"wrote {path} ({len(payload):,} bytes)", fg=typer.colors.GREEN)


@app.command(rich_help_panel="Start here")
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
            "  neti init                            from the MCP servers on this machine\n"
            "  neti init --example coding-agent     for a coding agent",
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


@app.command(rich_help_panel="When you need it")
def version() -> None:
    from neti import __version__

    typer.echo(__version__)


EXAMPLES = ("coding-agent", "entra", "data-agent", "infra-agent")


def resolve_policy(config: str, *, fatal: bool = True) -> Path:
    """A policy path, or an exit that says how to get one. Never a raw errno.

    Walking the documented flow on a clean install turned up four commands answering things like

        error: [Errno 2] No such file or directory: 'neti.yaml'

    and one — `neti demo` — handing the default string straight to `open()` and ending in a
    `FileNotFoundError` traceback. Every one of those is somebody's first run, and the difference
    between a tool that is missing a file and a tool that is broken is entirely in this message.

    Falls back to the shipped example, so the defaults that name one (`examples/entra.yaml`) work on
    an install as well as in a checkout.
    """
    path = Path(config)
    if path.exists():
        return path

    packaged = _packaged_example(config)
    if packaged is not None:
        return packaged

    # `fatal=False` is for `neti hook`, and only for it. A `PreToolUse` hook that exits non-zero
    # fails the tool call it was asked about, so it needs the sentence without the exit: the caller
    # then lets its own handler report the missing file and exit 0, saying out loud that nothing was
    # gated. Everywhere else, stopping is the right answer.
    if not fatal:
        return Path(config)

    typer.secho(f"error: no policy at {config}", fg=typer.colors.RED, err=True)
    typer.echo(
        "\nWrite one first:\n"
        "  neti init                            from the MCP servers on this machine\n"
        "  neti init --example coding-agent     Claude Code's Read, Edit, Glob, Grep, Bash\n"
        "  neti init --example entra            a directory: Microsoft 365 / Entra ID\n"
        f"\nOr point --config at one you already have. Shipped examples: {', '.join(EXAMPLES)}.",
        err=True,
    )
    raise typer.Exit(2)


def _records_flag(what: str) -> str:
    """`\u0060neti report\u0060` -> `neti report`, so the follow-up command is copy-pasteable."""
    return what.strip("`")


def resolve_records(records: str, *, what: str) -> Path:
    """A records path, or an exit that says how to get some.

    `report`, `propose` and `verify` all read a file the gate writes as it decides, and all three
    answered a bare `Errno 2` before anyone had run the gate — which is the exact moment a reader
    decides whether this product works.
    """
    path = Path(records)
    if path.exists():
        return path

    typer.secho(f"error: no decision records at {records}", fg=typer.colors.RED, err=True)
    typer.echo(
        f"\n{what} reads what the gate wrote as it decided, and nothing has been decided yet.\n\n"
        "  neti install                        wire the hook into Claude Code, then work\n"
        "                                      normally for an afternoon\n"
        "  neti prove                          one call through every door here, right now.\n"
        "                                      Writes out/proof.ndjson, so follow it with\n"
        f"                                      {_records_flag(what)} -r out/proof.ndjson\n"
        "\nNothing to record yet, and want a number anyway? `neti demo --here` measures what an\n"
        "agent could reach on this machine with no traffic and no credentials at all.",
        err=True,
    )
    raise typer.Exit(2)


def _destination(provider: str, base_url: str | None) -> str:
    """Where the request is going, said plainly enough to be checked."""
    if provider == "local":
        from neti.insight.assist_client import LOCAL_BASE_URL

        return f"{base_url or LOCAL_BASE_URL} — a model on this machine, nothing leaves it"
    return f"{provider}, using your key from this shell"


def _probe_call(tool: str, args: dict[str, Any]) -> str:
    """`remove_group_members(group='g-eng-all')`, for an error message to name."""
    return f"{tool}({', '.join(f'{k}={v!r}' for k, v in args.items())})"


def _write_example(name: str, target: Path, *, quiet: bool = False) -> None:
    """Copy a shipped policy into place, for the machine discovery cannot help.

    `neti init` reads MCP client configs, and plenty of people have none: a Claude Code user gating
    the built-in `Read`, `Edit`, `Glob` and `Bash` has no MCP server anywhere. That case used to end
    at

        cp examples/coding-agent.yaml neti.yaml    for a coding agent

    which is a directory that exists in a git checkout and nowhere on a real install. So the advice
    the CLI printed at the exact moment somebody was stuck could not be followed. This is the
    command that advice now names.
    """
    source = _packaged_example(f"{name}.yaml")
    if source is None:
        typer.secho(f"error: no shipped example called {name!r}.", fg="red", err=True)
        typer.echo(f"Available: {', '.join(EXAMPLES)}", err=True)
        raise typer.Exit(2)

    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    if quiet:
        # `neti start` narrates the whole first run itself. Letting this print its own "Next:"
        # block in the middle of that gave a newcomer two competing sets of instructions three
        # lines apart, which is precisely the confusion `start` exists to remove.
        return
    typer.secho(f"Wrote {target}", bold=True)
    typer.echo(
        f"  from the shipped {name} example, in observe mode with every ceiling blank.\n"
        "  Nothing is blocked until you write a number into it.\n\n"
        "Next:\n"
        f"  neti inventory -c {target}     what one call could reach, before any traffic\n"
        "  neti install                   wire it into Claude Code"
    )


def _packaged_example(name: str) -> Path | None:
    """The shipped example, from a source checkout or from wherever the demo is being run.

    Tried in order rather than assumed, because `--here` is the command a stranger runs first and
    "file not found" is a worse first impression than any finding is a good one.

    **The packaged copy comes first, and it is the one that was missing.** The list used to start at
    `parents[2]`, which is the repository root from `src/neti/cli.py` and `lib/python3.12/` from
    `site-packages/neti/cli.py`. So every candidate assumed a source checkout, and the README's
    opening command failed for everyone who had actually installed the thing. The wheel now carries
    `examples/` (see pyproject) and this looks there first.
    """
    # Callers pass both shapes. `demo --here` asks for "coding-agent.yaml"; `prove`, `inventory`,
    # `report` and `propose` default to the literal string "examples/entra.yaml" and hand that
    # straight through, which looked for `neti/examples/examples/entra.yaml` and found nothing. So
    # `neti prove` with no arguments answered "error: no policy at examples/entra.yaml" on every
    # install — including the one `neti demo --here` recommends running next.
    base = Path(name).name
    for candidate in (
        Path(__file__).resolve().parent / "examples" / base,
        Path(__file__).resolve().parents[2] / "examples" / base,
        Path.cwd() / "examples" / base,
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
        # The likeliest first run there is ends here, with four of six acts waiting on traffic
        # somebody has not generated yet. `neti prove` needs none: it is the one thing that can be
        # shown in full, right now, on a machine with nothing configured.
        typer.echo("")
        typer.secho(
            "Nothing to wait for on the other half: `neti prove` runs one call through every door\n"
            "this machine has and shows they all reach the same verdict, with a chain you can\n"
            "re-check yourself.",
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
    typer.echo("")
    typer.secho(
        "Six acts about one runtime. `neti prove` runs one call through every door this machine\n"
        "has — the hook, both MCP transports, and whichever agent SDKs are installed — and shows\n"
        "they reach the same verdict, the same magnitude and the same sentence.",
        fg=typer.colors.BRIGHT_BLACK,
    )


def main() -> int:
    use_utf8_output()
    app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
