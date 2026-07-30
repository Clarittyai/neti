"""Scripted agent runs — the story a viewer watches go through the gate.

The script is **data**. The server hands over an ordered plan and the browser drives each step
through the same `POST /api/gate` every other call uses. No server-side pacing, no background task,
no second code path. A demo with its own execution path stops being evidence the moment the two
diverge, and they always diverge.

The scenario is deliberately sympathetic: nobody is attacking anything. An agent is asked to
offboard contractors, works correctly, and calls `remove_group_members` on a group that someone
nested badly two years ago. That is the product's whole thesis — the damage in the incident corpus
was authorized, routine, and nobody found out until after.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["SCENARIOS", "Scenario", "Step"]


@dataclass(frozen=True)
class Step:
    tool: str
    args: dict[str, str]
    narration: str
    """What the agent believes it is doing. Shown above the call, in the agent's voice."""

    expect: str
    """The verdict this step is expected to reach. Displayed nowhere — it is a test fixture, so a
    scenario that silently stops demonstrating anything fails in CI rather than on stage."""


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    prompt: str
    """The human's original instruction. On screen this frames everything: the agent did what it
    was asked."""

    steps: tuple[Step, ...]
    moral: str
    session_id: str = "scenario"
    tags: tuple[str, ...] = field(default_factory=tuple)

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "moral": self.moral,
            "session_id": self.session_id,
            "steps": [
                {"tool": s.tool, "args": s.args, "narration": s.narration} for s in self.steps
            ],
        }


OFFBOARD = Scenario(
    id="offboard",
    title="Offboard the Q3 contractors",
    prompt=(
        "The Q3 contractor engagement ended Friday. Please revoke their access and let them know."
    ),
    session_id="offboard-demo",
    steps=(
        Step(
            tool="read_group",
            args={"group": "g-team"},
            narration="Finding the contractor group so I know who is affected.",
            # read_group is not gated: an ungated tool is out of scope, not denied (SCOPE.md NC-09)
            expect="allow",
        ),
        Step(
            tool="send_email",
            args={"to": "g-team"},
            narration="Sending the 25 contractors a note that their access ends today.",
            expect="allow",
        ),
        Step(
            tool="remove_group_members",
            args={"group": "g-eng-all"},
            narration=(
                "Removing them from the engineering group so the access is actually revoked."
            ),
            # The whole point. `engineering-all` reads like the right group and contains 41,203
            # people through nesting nobody has looked at in two years.
            expect="block",
        ),
    ),
    moral=(
        "Nothing here was an attack. The agent was asked to do something reasonable, chose a "
        "plausible group, and every check upstream said yes — the tool was permitted and the group "
        "was on the allowlist. The only thing that separated a routine offboarding from removing "
        "41,203 people was knowing how big the group actually was."
    ),
    tags=("identity", "blast-radius"),
)


UNSIZEABLE = Scenario(
    id="unsizeable",
    title="A target the gate cannot size",
    prompt="Email everyone in all-customers about the maintenance window.",
    session_id="unsizeable-demo",
    steps=(
        Step(
            tool="send_email",
            args={"to": "g-ddg"},
            narration="Mailing the all-customers list about tonight's maintenance.",
            # An Exchange dynamic distribution group: Graph 404s on it, so the gate does not know.
            expect="confirm",
        ),
    ),
    moral=(
        "`all-customers` is an Exchange dynamic distribution group. Exchange computes its "
        "membership and never syncs it to Entra, so Graph cannot count it. The gate does not guess "
        "and it does not read a failed lookup as zero — it says it does not know, and the declared "
        "policy decides what happens to a call nobody can size."
    ),
    tags=("unresolvable", "honesty"),
)


SCENARIOS: dict[str, Scenario] = {s.id: s for s in (OFFBOARD, UNSIZEABLE)}
