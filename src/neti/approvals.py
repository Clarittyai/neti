"""Approvals — the human `CONFIRM` has always been asking for.

`CONFIRM` means *a person other than the agent's operator should decide this one*. Until now the
gate said exactly that and then asked nobody: the MCP path returned `isError`, and the Claude Code
hook returned `ask`, which prompts the very person running the agent. For one developer at their
own laptop that is the right answer. For an organisation it is a verdict with no addressee.

Asking somebody else needs somewhere for the request to go and somewhere for the answer to come
back, which is why this is the one capability that cannot live on a single machine. What lives
*here* — in the free, Apache-2.0 package — is the protocol and the client for it. Both are useless
without a control plane, and neither checks a licence.

## What a grant is

**One execution of one call under one policy.** Four bindings make that true, and each one exists
because its absence is an exploit:

1. **The request digest.** `blake2b(canonical_json({tool, args, policy_digest, session_id}))`.
   Without it, a grant for `remove_group_members(g-small)` is redeemable against `g-eng-all` — the
   human approved one sentence and the agent executed a different one.
2. **Single use.** Consumed atomically on redemption, or "approve once" becomes "approve forever".
3. **An expiry.** A grant outliving the reviewer's attention is a grant nobody is thinking about.
4. **The approved magnitude, as a ceiling.** The grant records the number the human actually saw. On
   redemption the gate re-resolves — one O(1) count, the same one it already makes — and refuses if
   the target has grown past it. This closes a TOCTOU window `SCOPE.md` NC-08 otherwise leaves wide
   open: someone approves 40 people at 17:00, the group is nested into overnight, and at 09:00 the
   grant executes against 40,000. An approved figure is a ceiling like any other.

## Waiting

A human is slow; a tool call is not, and every MCP client has its own timeout. So the gate waits a
bounded `wait_s`, and if nobody has answered it returns *pending* with the approval id and an
instruction to retry the identical call. The agent retries naturally, the retry finds a granted,
unredeemed, digest-matching approval, and the call proceeds. That retry path is what lets a
two-minute human decision live inside a thirty-second tool call.

## When the control plane is not there

`Policy.on_approval_unavailable` decides, and it defaults to `block`. Unreachable, absent or unpaid
therefore all mean *the call does not proceed* — which is precisely what the free tier does with a
`CONFIRM` today. The control plane can only ever make a decision **more** permissive, and only
through a named human. Paying us adds no availability risk to enforcement, and that is a property,
not a promise.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from neti.core.canonical import canonical_bytes
from neti.core.types import ProposedCall

__all__ = [
    "Approval",
    "ApprovalState",
    "Approver",
    "ApproverError",
    "request_digest",
]


class ApproverError(Exception):
    """The control plane could not be reached or could not answer.

    Deliberately distinct from a denial. An operator debugging a stopped call needs to know whether
    a human said no or whether nobody was asked, and collapsing the two is how a broken integration
    gets mistaken for a working policy.
    """


class ApprovalState(StrEnum):
    PENDING = "pending"
    GRANTED = "granted"
    DENIED = "denied"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Approval:
    """One answer about one call."""

    id: str
    state: ApprovalState
    digest: str
    """The request this grant is bound to. A grant that does not match is not this call's grant."""

    approved_magnitude: int | None = None
    """What the human saw. Redemption refuses a target that has grown past it."""

    unit: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    reason: str | None = None
    expires_at: str | None = None

    @property
    def proceeds(self) -> bool:
        return self.state is ApprovalState.GRANTED


def request_digest(call: ProposedCall, policy_digest: str) -> str:
    """Content address of the call this approval would authorise.

    Includes the policy digest because a grant issued under one set of ceilings must not be
    redeemable under another — change the ceilings and every outstanding approval stops matching,
    which is the correct and conservative behaviour.

    `session_id` is deliberately *excluded*. It identifies a run, not a call, and including it would
    mean an agent that reconnects mid-approval could never redeem the grant it is waiting on.
    """
    from hashlib import blake2b

    payload: dict[str, Any] = {
        "tool": call.tool,
        "args": call.args,
        "policy_digest": policy_digest,
    }
    return blake2b(canonical_bytes(payload), digest_size=16).hexdigest()


class Approver(Protocol):
    """The client side of a control plane. Implemented in `neti_cloud`, never here.

    Two methods, because the two moments are genuinely different: `find` is the cheap check a retry
    makes, and `request` is the expensive one that raises a request and waits.
    """

    def find(self, digest: str) -> Approval | None:
        """An existing answer for this exact call, or `None`. Never raises for "not found"."""
        ...

    def request(self, call: ProposedCall, digest: str, evidence: dict[str, Any]) -> Approval:
        """Raise a request and wait up to the configured budget.

        Returns a `PENDING` approval on timeout rather than raising — nobody answering is a normal
        outcome and the agent needs the id so its retry can find the grant.
        """
        ...

    def redeem(self, approval: Approval, magnitude: int | None) -> Approval:
        """Consume a grant for one execution.

        `magnitude` is what the target resolves to *now*. The server refuses if it exceeds what was
        approved, and refuses a second redemption of the same grant. Returns the approval in its
        final state; the caller reads `.proceeds`.
        """
        ...
