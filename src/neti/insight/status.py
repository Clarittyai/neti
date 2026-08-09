"""Is this thing on?

**The question a silent control cannot answer about itself.** A gate that is working correctly on
an ordinary week does nothing visible: no verdict fires, no notification appears, the hook returns
nothing and the agent proceeds. That is the design — a control that interrupts ordinary work is a
control that gets switched off — but it leaves an operator unable to tell three states apart:

    working, and nothing happened          the good case
    wired to a policy that moved           silent, and protecting nothing
    never wired at all                     silent, and protecting nothing

All three look identical from a terminal, and two of them are somebody believing they are protected
while they are not. That is the failure this repository keeps finding in other shapes: a capability
that ships without a way to see it. `neti report` answers *what happened*, which is the wrong
question when the answer is "nothing" — it cannot distinguish nothing-happened from nothing-is-on.

So this answers *what is true right now*, from four independent places, and says which of them it
could not check rather than reporting a clean bill for a thing it did not look at:

1. the hook, read out of `.claude/settings.json` as Claude Code will read it
2. the policy that hook points at — **the same file?**, which is the failure mode a move creates
3. what that policy actually protects, counted rather than described
4. the chain: how much it has seen, and when it last saw anything

Nothing here is a measurement of a tree or a call to a provider. It reads four files and reports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["Status", "build_status"]


@dataclass
class Check:
    """One thing that is either true, false, or unknown — and unknown is never rendered as fine."""

    label: str
    ok: bool | None
    detail: str = ""

    @property
    def mark(self) -> str:
        return {True: "ok", False: "NO", None: "??"}[self.ok]


@dataclass
class Status:
    root: Path
    config: Path
    checks: list[Check] = field(default_factory=list)
    protects: list[str] = field(default_factory=list)
    seen: int = 0
    last_at: str | None = None
    stopped: int = 0
    fix: str = ""
    """The one command that would repair whatever is wrong. Empty when nothing is."""

    @property
    def live(self) -> bool:
        """Wired, enforcing, and pointed at this policy. Anything unknown is not live."""
        return all(c.ok for c in self.checks)


def _hook_command(root: Path) -> tuple[str | None, str]:
    """The `neti hook` command Claude Code would run here, and where it was read from.

    Used **only to say what a mis-wired hook points at**. Whether it is wired correctly is answered
    by `plan_install(...).already_installed`, which is the same comparison `neti install` itself
    makes — a second implementation of that would be a second copy of a fact, and this repository
    has learned three times what happens to those. What `already_installed` cannot express is the
    difference between *absent* and *pointing somewhere else*, and that difference is the whole
    product of a status command, so the parse stays for the message and not for the verdict.

    Project settings first, then the user-level file, because that is the order Claude Code merges
    them and a gate wired only at `~/.claude` protects this project too. Reporting "not wired" to
    somebody who wired it globally would send them to install it twice — and two hooks both fire,
    doubling every decision and every entry in the chain.
    """
    for scope, path in (
        ("project", root / ".claude" / "settings.json"),
        ("user", Path.home() / ".claude" / "settings.json"),
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        for entry in (data.get("hooks") or {}).get("PreToolUse") or []:
            if not isinstance(entry, dict):
                continue
            for hook in entry.get("hooks") or []:
                command = str(hook.get("command", ""))
                if "neti hook" in command:
                    return command, scope
    return None, ""


def _configured_policy(command: str, root: Path) -> Path | None:
    """The policy path inside a wired hook command, absolute."""
    tokens = command.split()
    for flag in ("-c", "--config"):
        if flag in tokens:
            at = tokens.index(flag) + 1
            if at < len(tokens):
                candidate = Path(tokens[at])
                return candidate if candidate.is_absolute() else (root / candidate).resolve()
    return None


def settings_file(root: Path) -> str:
    """Whichever settings file exists but does not parse, for an error message that names it."""
    for path in (root / ".claude" / "settings.json", Path.home() / ".claude" / "settings.json"):
        if path.exists():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return str(path)
    return ""


def _installed(root: Path, policy: Path) -> bool | None:
    """Whether the wired command is the one `neti install` would write for this policy.

    `None` when the settings file cannot be parsed: settings we cannot read are settings we must not
    conclude anything about, and `Check` renders that as `??` rather than as fine.
    """
    from neti.insight.install import plan_install

    for user in (False, True):
        try:
            if plan_install(root, policy.resolve(), user=user).already_installed:
                return True
        except Exception:
            return None
    return False


def build_status(root: str | Path, config: str | Path) -> Status:
    """Read the four places and report. No provider calls, no walk of the tree."""
    root_path = Path(root).resolve()
    config_path = Path(config)
    config_path = config_path if config_path.is_absolute() else (root_path / config_path)
    status = Status(root=root_path, config=config_path)

    # 1. the policy itself
    policy = None
    if not config_path.exists():
        status.checks.append(Check("a policy here", False, f"{config_path.name} does not exist"))
        status.fix = "neti start"
        return status
    try:
        from neti.config.policy import load_policy

        policy = load_policy(config_path)
        status.checks.append(Check("a policy here", True, str(config_path)))
    except Exception as exc:
        # Bare `Exception`, deliberately: every way a policy can fail to load is the same answer to
        # the operator, and a status command that raised on an unexpected one would be reporting a
        # crash instead of the fact it exists to report. `neti hook` exits 0 on a policy error, so a
        # broken file is not loud — it is a session running entirely ungated with the reason on
        # stderr where nothing reads it, which is precisely what this is here to surface.
        status.checks.append(Check("the policy loads", False, str(exc)[:120]))
        status.fix = f"neti verify -c {config_path.name}"
        return status

    # 2. the hook — asked of `plan_install`, which is what `neti install` itself compares
    command, scope = _hook_command(root_path)
    wired = _installed(root_path, config_path)

    if wired is None:
        # Settings we cannot parse are settings we must not conclude anything about — in either
        # direction. Reporting "not wired" here would send somebody to `neti install`, which
        # refuses to overwrite an unreadable file and would fail in front of them; reporting wired
        # would be worse. `??` is the honest mark and the fix names the real problem.
        status.checks.append(
            Check("wired into Claude Code", None, "settings file could not be parsed")
        )
        status.fix = f"fix the JSON in {settings_file(root_path) or '.claude/settings.json'}"
    elif command is None and not wired:
        status.checks.append(Check("wired into Claude Code", False, "no PreToolUse hook found"))
        status.fix = "neti install"
    else:
        status.checks.append(
            Check("wired into Claude Code", True, f"{scope or 'project'} settings")
        )
        # The failure a move creates, and the reason this is a separate line: the hook is present,
        # the command runs, every call really is gated — against a policy that is not this one.
        # Every other signal looks healthy, which is what makes it worth its own check.
        wired_to = _configured_policy(command or "", root_path)
        status.checks.append(
            Check(
                "wired to THIS policy",
                bool(wired),
                str(config_path) if wired else f"points at {wired_to or 'another policy'}",
            )
        )
        if not wired:
            status.fix = "neti install"

    # 3. enforcing
    mode = policy.mode.name.lower()
    status.checks.append(
        Check(
            "enforcing",
            mode == "enforce",
            mode if mode == "enforce" else f"{mode} — decisions are recorded, nothing is stopped",
        )
    )
    if mode != "enforce" and not status.fix:
        status.fix = f"set `mode: enforce` in {config_path.name}"

    # 4. what it protects, counted
    gates = [(t, p) for t, spec in policy.tools.items() for p in spec.gate]
    with_ceiling = [1 for t, p in gates if policy.gate_specs(t)[p].has_ceiling]
    status.protects = [
        f"{len(with_ceiling)} of {len(gates)} gated parameters have a ceiling",
        f"{len(policy.sensitive)} off-limits rule(s)",
        (
            "outside this directory asks first"
            if policy.outside_root is not None
            else "nothing about where a target is"
        ),
        f"{len(policy.session_budgets)} session budget(s)",
    ]
    if not with_ceiling and not policy.sensitive and policy.outside_root is None:
        status.checks.append(Check("protects something", False, "no ceiling, no rule, no location"))
        status.fix = status.fix or "neti start"

    return status


def observed(records: str | Path) -> tuple[int, str | None, int, int]:
    """How much the chain holds, when it last grew, how much of it was stopped, and what is torn.

    A count and a timestamp rather than a verdict on freshness. "Last decision 6 days ago" means
    something different to somebody who has been on holiday than to somebody who shipped this
    morning, and this does not know which — so it reports the fact and lets them read it.
    """
    path = Path(records)
    seen = stopped = torn = 0
    last: str | None = None
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    # Counted, not skipped. A line the reader cannot parse is a gap in the audit
                    # trail, and the trail is the product's whole claim — silently passing over it
                    # would leave the one screen that exists to report health reporting none.
                    torn += 1
                    continue
                seen += 1
                last = row.get("decided_at") or last
                if row.get("verdict") in {"confirm", "block"}:
                    stopped += 1
    except OSError:
        return 0, None, 0, 0
    return seen, last, stopped, torn


def ago(timestamp: str | None) -> str:
    """`2026-08-08T09:14:02+00:00` as something a person reads without doing arithmetic."""
    if not timestamp:
        return "never"
    try:
        when = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = (datetime.now(UTC) - when).total_seconds()
    if seconds < 90:
        return "just now"
    for size, unit in ((3600, "minute"), (86400, "hour"), (86400 * 7, "day")):
        if seconds < size * (60 if unit == "minute" else 24 if unit == "hour" else 7):
            count = int(
                seconds // (size / (60 if unit == "minute" else 24 if unit == "hour" else 7))
            )
            if count:
                return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return f"{int(seconds // 86400)} days ago"


def render(status: Status, seen: int, last: str | None, stopped: int, torn: int = 0) -> str:
    """One screen. The verdict first, because that is the whole question."""
    out: list[str] = []
    headline = (
        "neti is on and enforcing." if status.live else "neti is NOT protecting this directory."
    )
    out += [headline, ""]

    width = max(len(c.label) for c in status.checks)
    for check in status.checks:
        out.append(f"  [{check.mark}]  {check.label:<{width}}   {check.detail}")

    if status.protects:
        out += ["", "  What it protects here:"]
        out += [f"     {line}" for line in status.protects]

    out += ["", "  What it has seen:"]
    if seen:
        out.append(f"     {seen:,} decision(s), last one {ago(last)}")
        out.append(f"     {stopped:,} of them stopped a call")
    if torn:
        # Enforcement survives a torn record — `neti hook` says so on stderr and in the payload, and
        # the decision is made before it is filed. What does not survive is the audit trail, which
        # is the other half of what this product sells, so it is said here rather than left for
        # whoever thinks to run `neti verify`.
        entries = "entry" if torn == 1 else "entries"
        out.append(f"     {torn:,} {entries} could not be read — the chain has a gap.")
        out.append("     Those decisions were still enforced. `neti verify` shows where.")
    else:
        # The honest reading of an empty chain, which is not "you are safe".
        out.append("     nothing yet — no call has reached the gate")
        if status.live:
            out.append("     (expected on a fresh install; if your agent has been running, it is")
            out.append("      not going through the hook)")

    if status.fix:
        out += ["", f"  Fix:  {status.fix}"]
    return "\n".join(out)


def as_json(
    status: Status, seen: int, last: str | None, stopped: int, torn: int = 0
) -> dict[str, Any]:
    """The same answer for a script — an exit code alone cannot say which check failed."""
    return {
        "live": status.live,
        "root": str(status.root),
        "config": str(status.config),
        "checks": [{"label": c.label, "ok": c.ok, "detail": c.detail} for c in status.checks],
        "protects": status.protects,
        "decisions": seen,
        "last_decision_at": last,
        "stopped": stopped,
        "unreadable": torn,
        "fix": status.fix or None,
    }
