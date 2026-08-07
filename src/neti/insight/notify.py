"""Telling the human, at the moment it happens.

`Verdict.FLAG` is documented as *"Recorded and **surfaced**; the call proceeds."* The surfacing half
did not exist. A flagged deletion sat in a record file until somebody ran `neti report`, which means
that on a real machine *flagged* and *silent* were the same experience — and `flag` is precisely the
verdict for a call that must not be stopped and must not be quiet.

**Only `flag` notifies by default,** and the reason is that the others already speak. A `block` and
a `confirm` are handed back to the agent as a sentence with a number in it, which is the whole
interaction; the operator finds out because their agent tells them it was stopped. A `flag` is the
one verdict where the call proceeds and nothing anywhere says so.

Three rules, in the order they matter:

**It can never break the hook.** `neti hook` runs on every tool call in a Claude Code session, and
an exception escaping it is not one failed call, it is every subsequent call in the session failing
until somebody works out that a hook is the cause. So every path here is inside a bare `except`, and
the failure mode is a missing notification rather than a broken session.

**It can never add latency.** The hook measures p50 137ms and most of that is interpreter start. The
notifier is spawned and abandoned — no `wait`, no pipe to read — so it costs a `fork` and nothing
else.

**The text is untrusted.** It contains the command the agent proposed, and an agent can be prompt-
injected; a command carrying `"` or `'` is not hypothetical. Nothing here interpolates it into a
script or a shell string. macOS gets it through `on run argv`, Linux through `notify-send`'s argv,
and both are parsed as data by construction rather than by escaping carefully enough.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass

__all__ = ["DEFAULT_ON", "notify"]

DEFAULT_ON = ("flag",)
"""The verdict whose own docstring promises surfacing, and the only one nothing else surfaces."""

_MAX = 120
"""Notification bodies are truncated by every platform anyway; doing it here keeps the argv small
and stops a 200KB argument from an agent reaching a subprocess at all."""


# `display notification` with the text as `argv`, never interpolated into the script. The obvious
# version — building an AppleScript string with the command inside it — is a script-injection hole
# reachable by anything that can influence what the agent proposes, which is the exact threat this
# product exists to gate.
_APPLESCRIPT = """
on run argv
    display notification (item 1 of argv) with title (item 2 of argv) subtitle (item 3 of argv)
end run
"""


@dataclass(frozen=True)
class Notice:
    verdict: str
    tool: str
    target: str
    detail: str = ""

    @property
    def title(self) -> str:
        return f"neti — {self.verdict}"

    @property
    def subtitle(self) -> str:
        return self.tool

    @property
    def body(self) -> str:
        text = self.target if len(self.target) <= _MAX else f"{self.target[: _MAX - 1]}…"
        return f"{text}\n{self.detail}" if self.detail else text


def _spawn(argv: list[str]) -> None:
    """Start it and walk away. No pipes to drain, no exit status to wait for."""
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _macos(notice: Notice) -> bool:
    # `-e` per line, then the text as trailing argv. `osascript -` would read the script from stdin,
    # which `_spawn` deliberately closes — the notifier is abandoned on spawn and has nobody to feed
    # it. Caught by running it: the first version posted nothing at all, silently, which is a
    # notably poor failure for the feature whose entire job is to stop being silent.
    argv = ["osascript"]
    for line in _APPLESCRIPT.strip().splitlines():
        argv += ["-e", line]
    _spawn([*argv, notice.body, notice.title, notice.subtitle])
    return True


def _linux(notice: Notice) -> bool:
    if shutil.which("notify-send") is None:
        return False
    _spawn(["notify-send", "--app-name=neti", notice.title, notice.body])
    return True


def notify(verdict: str, tool: str, target: str, detail: str = "", *, on: tuple[str, ...]) -> bool:
    """Post an OS notification for this decision, if the policy asked for one.

    Returns whether one was attempted, which is all a caller can honestly know — the notifier is
    abandoned on spawn, so whether it *appeared* is between the operating system and the user's
    focus settings.

    Never raises. A machine with no notification daemon, a headless CI box, a locked-down
    `osascript` — every one of those is a missing notification and a session that keeps working.
    """
    try:
        if verdict not in on:
            return False
        # A gate running in CI has no desktop to notify, and spawning `osascript` a thousand times
        # into a headless session is a waste at best.
        #
        # Deliberately **not** an `isatty` check, which is the obvious-looking one and is wrong
        # here: `neti hook` reads its event from a pipe, so stdin is never a tty in the exact place
        # this feature exists to serve. That version would have disabled notifications everywhere
        # while looking like a thoughtful guard.
        if os.environ.get("CI") or os.environ.get("NETI_NO_NOTIFY"):
            return False

        notice = Notice(verdict=verdict, tool=tool, target=target, detail=detail)
        system = platform.system()
        if system == "Darwin":
            handled = _macos(notice)
        elif system == "Linux":
            handled = _linux(notice)
        else:
            # Windows toasts need a PowerShell payload with the text inside it, which is the
            # injection shape this module refuses everywhere else. Left undone rather than done
            # unsafely; `neti report` still has it.
            handled = False
        return handled
    except Exception:
        # Deliberately every exception. See the module docstring: a notification must never be able
        # to take a session down, and a missing one is a cosmetic loss.
        return False
