"""Where is this install, and what is the next thing to do about it.

The console opened on an overview: a large number nobody had asked for, two zeros, and a warning
that nine parameters had no ceiling. Every one of those is true and none of them answers the two
questions somebody has on their first run — **what is this**, and **how do I make it work with what
I actually use.** The answer lived in `docs/TUTORIAL.md`, which is not in the product.

So this reads the machine and returns the walkthrough as *state*: five steps, each one already
ticked or not, each one naming the real path and the real command for the agent this person is
running. Not a document about a generic install — a checklist about theirs.

Two properties make it a tutorial rather than a page of instructions:

**It is derived, never stored.** There is no "onboarding complete" flag anywhere, because a flag
would go stale the moment somebody uninstalled the hook or swapped a policy, and a checklist that
lies about your machine is worse than none. Every step re-reads the world. Uninstall the hook and
step two un-ticks itself.

**It is cheap enough to poll.** Reading a handful of JSON config files and counting records, with
no filesystem walk and no provider call — so the console can ask every few seconds and the step
ticks itself the moment the first real call lands, while the reader is still looking at it. That
is the difference between a walkthrough you follow and one you watch happen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from neti.config.policy import Policy
from neti.core.verdict import Mode

__all__ = ["Harness", "StartState", "Step", "start_state"]


@dataclass(frozen=True)
class Harness:
    """One place on this machine an agent could call a tool from, and how the gate gets in front.

    `kind` is the seam, not the product: `hook` is the harness's own built-in tools, which no proxy
    can see, and `mcp` is a server the client launches as a subprocess. A person running Claude
    Code with three MCP servers has four of these and needs both answers.
    """

    kind: str
    label: str
    where: str
    gated: bool
    detail: str = ""
    command: str = ""
    """What to run, or the config to change. Real paths, because a generic snippet is the thing
    that makes somebody close the tab and go back to the README."""


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    why: str
    """What this step buys, in one sentence. A checklist with no *why* is a chore."""

    done: bool
    detail: str = ""
    command: str = ""
    doc: str = ""


@dataclass
class StartState:
    steps: list[Step] = field(default_factory=list)
    harnesses: list[Harness] = field(default_factory=list)
    decisions: int = 0
    gated_params: int = 0
    with_ceilings: int = 0
    mode: str = "observe"
    policy: str = ""
    enforcing: bool = False

    @property
    def complete(self) -> bool:
        return all(s.done for s in self.steps)

    @property
    def next_step(self) -> Step | None:
        return next((s for s in self.steps if not s.done), None)

    def as_json(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "next": None if self.next_step is None else self.next_step.id,
            "decisions": self.decisions,
            "gated_params": self.gated_params,
            "with_ceilings": self.with_ceilings,
            "mode": self.mode,
            "policy": self.policy,
            "steps": [
                {
                    "id": s.id,
                    "title": s.title,
                    "why": s.why,
                    "done": s.done,
                    "detail": s.detail,
                    "command": s.command,
                    "doc": s.doc,
                }
                for s in self.steps
            ],
            "harnesses": [
                {
                    "kind": h.kind,
                    "label": h.label,
                    "where": h.where,
                    "gated": h.gated,
                    "detail": h.detail,
                    "command": h.command,
                }
                for h in self.harnesses
            ],
        }


def _hook_harnesses(root: Path, policy: Path) -> list[Harness]:
    """Claude Code's `PreToolUse` hook, project and user scope.

    The project row is always shown, because it is the scope `neti install` defaults to and the one
    the policy is actually about — its ceilings came from this repository's traffic and its
    `providers.fs.root` names this tree. `neti install` creates the file, so "you have not got one"
    is a state of the step rather than a reason to hide it.

    The user row appears only when `~/.claude` is really there. Gating every session on the machine
    is a bigger commitment than this checklist should propose to somebody who has not already opted
    into that scope.
    """
    from neti.insight.install import plan_install, settings_path

    out: list[Harness] = []
    for user, label, flag in (
        (False, "Claude Code — this project", ""),
        (True, "Claude Code — every session", " --user"),
    ):
        path = settings_path(root, user=user)
        if user and not path.parent.exists():
            continue
        try:
            # **Resolved for the check, as given for the display.** `neti install` writes
            # `neti hook -c <absolute path>`, and `plan_install` decides "already installed" by
            # comparing that string — so planning against the relative path the console was
            # launched with (`--config examples/coding-agent.yaml`) never matches, and the
            # walkthrough tells somebody who is installed that they are not. The command shown
            # below keeps the path they typed, because that is the one that reads.
            plan = plan_install(root, policy.resolve(), user=user)
            installed = plan.already_installed
        except Exception:
            # Settings we cannot parse are settings we must not claim anything about. Reported as
            # ungated with the reason, rather than silently dropped — a missing row reads as "you
            # don't run this", which would be the wrong thing to tell somebody who does.
            out.append(
                Harness(
                    kind="hook",
                    label=label,
                    where=str(path),
                    gated=False,
                    detail="this settings file could not be parsed, so nothing was concluded",
                    command=f"neti install -c {policy}{flag}",
                )
            )
            continue
        out.append(
            Harness(
                kind="hook",
                label=label,
                where=str(path),
                gated=installed,
                detail=_hook_detail(installed, path),
                command="" if installed else f"neti install -c {policy}{flag}",
            )
        )
    return out


def _mcp_harnesses(root: Path) -> list[Harness]:
    """Every stdio MCP server the clients on this machine are configured to launch."""
    from neti.insight.discover import find_clients

    already: list[str] = []
    try:
        servers = find_clients(root, already_gated=already)
    except Exception:
        return []

    out = [
        Harness(
            kind="mcp",
            label=name,
            where="already wrapped by neti gate",
            gated=True,
            detail="calls to this server resolve before they are forwarded",
        )
        for name in sorted(set(already))
    ]
    for server in servers:
        launch = " ".join([server.command, *server.args])
        out.append(
            Harness(
                kind="mcp",
                label=server.name,
                where=f"{server.client} · {server.path}",
                gated=False,
                detail="the launch command becomes an argument to the gate; the server does not "
                "change and neither does the agent",
                command=f"neti gate --stdio -- {launch}",
            )
        )
    return out


def start_state(
    policy: Policy,
    *,
    policy_path: str | Path,
    decisions: int,
    root: Path | None = None,
) -> StartState:
    """The walkthrough, as facts about this machine.

    The order is the one in `docs/TUTORIAL.md` and it is not ceremony: a policy asks for a ceiling,
    and on day one nobody knows whether 300 is generous or absurd for their repository. So the
    number comes first, enforcement last, and the two steps in between are what turns a guess into
    a measurement.
    """
    root = root or Path.cwd()
    path = Path(policy_path)

    harnesses = [*_hook_harnesses(root, path), *_mcp_harnesses(root)]
    gated_params = sum(len(spec.gate) for spec in policy.tools.values())
    with_ceilings = sum(
        1 for spec in policy.tools.values() for gate in spec.gate.values() if gate.has_ceiling
    )
    enforcing = policy.mode is Mode.ENFORCE
    wired = any(h.gated for h in harnesses)

    steps = [
        Step(
            id="policy",
            title="Declare what is gated",
            why="Coverage is a declaration, not a guess. An ungated tool is out of scope, and neti "
            "says so rather than pretending otherwise.",
            done=gated_params > 0,
            detail=f"{gated_params} parameter(s) gated by {path}"
            if gated_params
            else "no tool is gated yet",
            command="" if gated_params else "neti init --example coding-agent",
            doc="/policy",
        ),
        Step(
            id="reach",
            title="See what one call could touch",
            why="Nothing else in your stack answers this. Authorization answers may you, "
            "sandboxing answers where, approval answers did a human say yes. None of them "
            "answers how big.",
            done=gated_params > 0,
            detail="read straight from this machine — no traffic needed, no credentials, "
            "nothing observed yet",
            doc="/",
        ),
        Step(
            id="install",
            title="Put the gate in front of your agent",
            why="Until this, neti can measure your machine but has never seen a call. This is the "
            "one step that touches a file you own, and it shows you the change first.",
            done=wired,
            detail=_install_detail(harnesses),
            command="" if wired else _install_command(harnesses, path),
            doc="/connect",
        ),
        Step(
            id="traffic",
            title="Work normally, and watch the decisions arrive",
            why="Nothing is blocked in observe mode. What you get is your agent's real "
            "distribution — which is what tells you whether 300 is generous or absurd here.",
            done=decisions > 0,
            detail=f"{decisions:,} decision(s) recorded" if decisions else "nothing recorded yet",
            doc="/decisions",
        ),
        Step(
            id="ceilings",
            title="Declare ceilings, then turn it on",
            why="`neti propose` reads your own traffic and suggests numbers, with the distribution "
            "behind each one. It prints a fragment; it never edits your policy.",
            done=with_ceilings > 0 and enforcing,
            detail=_ceiling_detail(with_ceilings, gated_params, enforcing),
            command="neti propose" if with_ceilings == 0 else "",
            doc="/policy",
        ),
    ]
    return StartState(
        steps=steps,
        harnesses=harnesses,
        decisions=decisions,
        gated_params=gated_params,
        with_ceilings=with_ceilings,
        mode=policy.mode.name.lower(),
        policy=str(path),
        enforcing=enforcing,
    )


def _hook_detail(installed: bool, path: Path) -> str:
    if installed:
        return "gated: every built-in tool call is sized before it runs"
    if not path.exists():
        return (
            "the harness's own tools — Bash, Read, Edit, Glob. No proxy can see these, and there "
            "is no settings file here yet; the install creates one."
        )
    return "the harness's own tools — Bash, Read, Edit, Glob. No proxy can see these."


def _install_detail(harnesses: list[Harness]) -> str:
    gated = [h for h in harnesses if h.gated]
    if gated:
        return f"gated: {', '.join(h.label for h in gated)}"
    if harnesses:
        return f"found on this machine: {', '.join(h.label for h in harnesses)}"
    # An honest empty state. Claiming to have detected nothing is different from claiming there is
    # nothing, and somebody running an agent this does not recognise needs the third door named.
    return (
        "no Claude Code settings and no MCP servers found here — the SDK seam takes any tool loop"
    )


def _install_command(harnesses: list[Harness], policy: Path) -> str:
    """The project hook first, then a server, then the bare command.

    Project scope leads because that is what `neti install` does with no flags, and because a
    checklist that proposes `--user` to somebody who has not asked for it is proposing to change
    every session on their machine.
    """
    hook = next((h for h in harnesses if h.kind == "hook" and not h.gated), None)
    if hook is not None:
        return hook.command
    mcp = next((h for h in harnesses if h.kind == "mcp" and not h.gated), None)
    return mcp.command if mcp is not None else f"neti install -c {policy}"


def _ceiling_detail(with_ceilings: int, gated: int, enforcing: bool) -> str:
    if with_ceilings == 0:
        return "no ceiling declared yet — every gate resolves and records, none can block"
    if not enforcing:
        return (
            f"{with_ceilings} of {gated} gate(s) have a ceiling, and the policy is still "
            "observing. Set mode: enforce when the numbers look right."
        )
    return f"enforcing, with {with_ceilings} of {gated} gate(s) declaring a ceiling"
