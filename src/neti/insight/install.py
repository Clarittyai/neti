"""Wiring the gate into Claude Code's settings, without asking anyone to edit JSON by hand.

Installing was four steps: write a policy, find `.claude/settings.json`, work out the `PreToolUse`
hook shape, and merge it into whatever is already there without breaking it. Every one of those is
somewhere an evaluator gives up, and the last is the worst — it is hand-editing a config file that
an agent depends on, with no feedback until the next session behaves strangely.

Three rules, because this writes to a file somebody else owns:

**Merge, never replace.** A `hooks` block usually has other entries in it, and clobbering them
would break tooling the user set up before they ever heard of this. The existing structure is read,
the neti entry is added or updated in place, and everything else is returned untouched.

**Idempotent.** Running it twice is a no-op rather than two hooks that both fire, double every
decision and write each one to the chain twice.

**Show the diff, and back the file up.** The install is small enough to read, so it is printed
before it happens. That is the difference between a tool somebody trusts with their config and one
they run once in a VM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["HOOK_EVENT", "InstallPlan", "plan_install", "settings_path"]

HOOK_EVENT = "PreToolUse"
MATCHER = "*"


def settings_path(root: Path, *, user: bool = False) -> Path:
    """Where the hook goes.

    Project-scoped by default (`.claude/settings.json`), because a policy is about a particular
    repository — its ceilings came from that repository's traffic, and `providers.fs.root` names
    its tree. `--user` writes `~/.claude/settings.json` for someone who wants every session gated.
    """
    return (Path.home() if user else root) / ".claude" / "settings.json"


@dataclass
class InstallPlan:
    path: Path
    policy: Path
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    already_installed: bool = False
    other_hooks: int = 0
    """How many `PreToolUse` entries belong to somebody else. Reported, because merging into a
    populated block is the case where a mistake is expensive and silent."""

    @property
    def command(self) -> str:
        return f"neti hook -c {self.policy}"

    @property
    def changed(self) -> bool:
        return self.before != self.after

    def diff(self) -> str:
        """What would change, as JSON a person can read before agreeing to it."""
        return json.dumps(self.after, indent=2, sort_keys=True)


def _entry(command: str) -> dict[str, Any]:
    return {"matcher": MATCHER, "hooks": [{"type": "command", "command": command}]}


def _is_neti(entry: dict[str, Any]) -> bool:
    return any("neti hook" in str(hook.get("command", "")) for hook in entry.get("hooks", []) or [])


def plan_install(root: Path, policy: Path, *, user: bool = False) -> InstallPlan:
    """Work out the edit without making it.

    Split from applying it so the command can print the result and stop — and so this is testable
    without a filesystem full of half-written settings.
    """
    path = settings_path(root, user=user)
    before: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            before = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            # A settings file we cannot parse is not one to overwrite. The caller reports this as a
            # refusal; guessing at the user's intent here would be how their config gets destroyed.
            raise

    plan = InstallPlan(path=path, policy=policy, before=before)
    after = json.loads(json.dumps(before))  # deep copy, so `before` stays the file as it was
    hooks = after.setdefault("hooks", {})
    entries = hooks.setdefault(HOOK_EVENT, [])
    if not isinstance(entries, list):
        raise ValueError(f"{path}: hooks.{HOOK_EVENT} is not a list")

    ours = [e for e in entries if isinstance(e, dict) and _is_neti(e)]
    plan.other_hooks = len(entries) - len(ours)

    if ours:
        # Already wired, possibly to a different policy. Update in place rather than appending a
        # second hook: two would both fire, doubling every decision and every chain entry.
        plan.already_installed = all(
            hook.get("command") == plan.command for e in ours for hook in e.get("hooks", []) or []
        )
        for entry in ours:
            entry["hooks"] = [{"type": "command", "command": plan.command}]
    else:
        entries.append(_entry(plan.command))

    plan.after = after
    return plan


def apply_install(plan: InstallPlan) -> Path | None:
    """Write it, keeping a copy of whatever was there. Returns the backup path, if one was made."""
    backup: Path | None = None
    if plan.path.exists():
        backup = plan.path.with_suffix(plan.path.suffix + ".neti-backup")
        backup.write_text(plan.path.read_text(encoding="utf-8"), encoding="utf-8")

    plan.path.parent.mkdir(parents=True, exist_ok=True)
    plan.path.write_text(json.dumps(plan.after, indent=2) + "\n", encoding="utf-8")
    return backup
