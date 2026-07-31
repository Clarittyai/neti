"""The approval protocol.

`CONFIRM` has always meant *a human other than the operator should decide this one*, and until now
nobody was asked. These tests pin down what a grant is allowed to authorise, and every one of them
exists because the absence of that binding is an exploit rather than an untidiness:

- a grant for a small call redeemed against a big one
- a grant redeemed twice
- a grant redeemed after the target grew
- a grant issued under one policy redeemed under another

`FakeApprover` here is the reference implementation of the `Approver` protocol — it is what the
control plane in `neti_cloud` has to behave like, and keeping it in-process means these properties
are tested without a server, a socket or a clock.

The last group is the one that keeps the commercial story honest: **with no approver, or with an
approver that cannot be reached, the gate behaves exactly as the free tier.** If those tests ever go
red, paying for approvals has started to carry availability risk, and the tier boundary described in
LICENSING.md is no longer true.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from neti.approvals import Approval, ApprovalState, ApproverError, request_digest
from neti.config.policy import Policy, load_policy
from neti.core.types import Band, ProposedCall
from neti.core.verdict import Mode, Verdict
from neti.engine import Engine
from neti.eval.synthetic import SyntheticTenant, default_tenant
from neti.gatekeeper import Gatekeeper
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from tests.integration.test_inventory import EXAMPLE

CRED = ClientCredential(tenant_id="d", client_id="d", client_secret="d")


@dataclass
class FakeApprover:
    """An in-process control plane: the reference the real one has to match.

    `answer` decides what a reviewer does. `None` means nobody has answered yet, which is the normal
    case and the one the wait-then-retry path is built around.
    """

    answer: ApprovalState | None = None
    approved_magnitude: int | None = None
    decided_by: str = "sam@acme.com"
    unreachable: bool = False

    issued: dict[str, Approval] = field(default_factory=dict)
    redeemed: set[str] = field(default_factory=set)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def find(self, digest: str) -> Approval | None:
        if self.unreachable:
            raise ApproverError("control plane unreachable")
        return self.issued.get(digest)

    def request(self, call: ProposedCall, digest: str, evidence: dict[str, Any]) -> Approval:
        if self.unreachable:
            raise ApproverError("control plane unreachable")
        self.requests.append({"digest": digest, **evidence})
        state = self.answer or ApprovalState.PENDING
        approval = Approval(
            id=f"a_{len(self.issued) + 1}",
            state=state,
            digest=digest,
            approved_magnitude=(
                self.approved_magnitude
                if self.approved_magnitude is not None
                else evidence.get("magnitude")
            ),
            unit=evidence.get("unit"),
            decided_by=self.decided_by if state is not ApprovalState.PENDING else None,
        )
        self.issued[digest] = approval
        return approval

    def redeem(self, approval: Approval, magnitude: int | None) -> Approval:
        if self.unreachable:
            raise ApproverError("control plane unreachable")
        if approval.id in self.redeemed:
            return self._refuse(approval, "already redeemed")
        # A grant carries the number the human saw. If the target has grown since, the sentence they
        # approved is no longer the sentence being executed.
        if approval.approved_magnitude is not None and (
            magnitude is None or magnitude > approval.approved_magnitude
        ):
            return self._refuse(approval, "target grew past the approved magnitude")
        self.redeemed.add(approval.id)
        return approval

    @staticmethod
    def _refuse(approval: Approval, reason: str) -> Approval:
        return Approval(
            id=approval.id,
            state=ApprovalState.EXPIRED,
            digest=approval.digest,
            approved_magnitude=approval.approved_magnitude,
            reason=reason,
        )


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


def build(tenant: SyntheticTenant, approver: Any = None, **policy_kw: Any) -> Gatekeeper:
    policy: Policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.ENFORCE, **policy_kw})
    client = GraphClient(CRED, transport=tenant.transport())
    engine = Engine(policy=policy, resolvers=resolvers_for_client(client))
    return Gatekeeper(engine=engine, approver=approver)


# `send_email` to a 500-member group: confirm above 50, block above 500. Squarely a CONFIRM.
CONFIRMING = ProposedCall(tool="send_email", args={"to": "g-dept"})
BLOCKING = ProposedCall(tool="remove_group_members", args={"group": "g-eng-all"})
FITTING = ProposedCall(tool="send_email", args={"to": "g-team"})


# ---------------------------------------------------------------------------- what a grant is


def test_a_granted_approval_lets_the_call_proceed(tenant: SyntheticTenant) -> None:
    keeper = build(tenant, FakeApprover(answer=ApprovalState.GRANTED))
    decision = keeper.decide(CONFIRMING)

    assert decision.proceeds
    # The verdict stays what the policy said. A person was asked; the ceiling did not move.
    assert decision.verdict.name == "CONFIRM"
    assert decision.escalation.approval is not None
    assert decision.escalation.approval.decided_by == "sam@acme.com"


def test_a_denied_approval_stops_the_call(tenant: SyntheticTenant) -> None:
    keeper = build(tenant, FakeApprover(answer=ApprovalState.DENIED))
    decision = keeper.decide(CONFIRMING)
    assert not decision.proceeds
    assert decision.escalation.state is ApprovalState.DENIED


def test_nobody_answering_leaves_the_call_stopped(tenant: SyntheticTenant) -> None:
    """Pending is not permission. The agent gets an id and retries later."""
    keeper = build(tenant, FakeApprover())
    decision = keeper.decide(CONFIRMING)
    assert not decision.proceeds
    assert decision.escalation.state is ApprovalState.PENDING
    assert decision.escalation.approval is not None
    assert decision.escalation.approval.id


def test_the_reviewer_is_shown_the_magnitude(tenant: SyntheticTenant) -> None:
    """The number is the entire reason a human is being asked rather than a policy engine."""
    approver = FakeApprover()
    build(tenant, approver).decide(CONFIRMING)
    (asked,) = approver.requests
    assert asked["magnitude"] == 500
    assert asked["unit"] == "recipients"
    assert asked["tool"] == "send_email"


# ---------------------------------------------------------------------------- the four bindings


def test_a_grant_is_bound_to_the_exact_call() -> None:
    """Otherwise a grant for a small group is redeemable against a big one."""
    policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.ENFORCE})
    small = request_digest(ProposedCall(tool="send_email", args={"to": "g-team"}), policy.digest())
    large = request_digest(ProposedCall(tool="send_email", args={"to": "g-dept"}), policy.digest())
    assert small != large


def test_a_grant_is_bound_to_the_policy_that_produced_it() -> None:
    """Move a ceiling and every outstanding approval stops matching, which is the safe direction.

    A grant means "a human looked at 500 recipients against a limit of 50 and said yes". Raise the
    limit to 5,000 and that sentence is no longer what anyone agreed to, so the grant must not
    survive the edit.
    """
    call = ProposedCall(tool="send_email", args={"to": "g-dept"})
    before = load_policy(EXAMPLE).model_copy(update={"mode": Mode.ENFORCE})

    raised = (
        before.tools["send_email"]
        .gate["/to"]
        .model_copy(update={"bands": (Band(above=5_000, verdict=Verdict.CONFIRM),)})
    )
    after = before.model_copy(
        update={
            "tools": {
                **before.tools,
                "send_email": before.tools["send_email"].model_copy(
                    update={"gate": {"/to": raised}}
                ),
            }
        }
    )

    assert before.digest() != after.digest()
    assert request_digest(call, before.digest()) != request_digest(call, after.digest())
    # Same policy, same call, same digest — otherwise no grant would ever be redeemable.
    assert request_digest(call, before.digest()) == request_digest(call, before.digest())


def test_a_grant_survives_a_reconnect(tenant: SyntheticTenant) -> None:
    """`session_id` is excluded on purpose: an agent that reconnects mid-approval must still be
    able to redeem the grant it is waiting on."""
    policy = load_policy(EXAMPLE)
    one = ProposedCall(tool="send_email", args={"to": "g-dept"}, session_id="s1")
    two = ProposedCall(tool="send_email", args={"to": "g-dept"}, session_id="s2")
    assert request_digest(one, policy.digest()) == request_digest(two, policy.digest())


def test_a_grant_is_single_use(tenant: SyntheticTenant) -> None:
    """ "Approve once" must not become "approve forever"."""
    approver = FakeApprover(answer=ApprovalState.GRANTED)
    keeper = build(tenant, approver)

    assert keeper.decide(CONFIRMING).proceeds
    second = keeper.decide(CONFIRMING)
    assert not second.proceeds
    assert second.escalation.approval is not None
    assert second.escalation.approval.reason == "already redeemed"


def test_a_grant_refuses_a_target_that_grew(tenant: SyntheticTenant) -> None:
    """The TOCTOU window SCOPE.md NC-08 leaves open.

    A reviewer approves 40 people at 17:00; the group is nested into overnight; at 09:00 the same
    grant would otherwise execute against 40,000. The approved figure is a ceiling like any other.
    """
    approver = FakeApprover(answer=ApprovalState.GRANTED, approved_magnitude=40)
    decision = build(tenant, approver).decide(CONFIRMING)  # resolves to 500 now

    assert not decision.proceeds
    assert decision.escalation.approval is not None
    assert decision.escalation.approval.reason == "target grew past the approved magnitude"


def test_a_retry_finds_the_existing_grant_instead_of_asking_again(tenant: SyntheticTenant) -> None:
    """The other half of wait-then-retry. Without it every retry re-notifies the same reviewer."""
    approver = FakeApprover()
    keeper = build(tenant, approver)

    keeper.decide(CONFIRMING)
    keeper.decide(CONFIRMING)
    keeper.decide(CONFIRMING)
    assert len(approver.requests) == 1


# ---------------------------------------------------------------------------- what is never asked


def test_a_block_is_never_escalated(tenant: SyntheticTenant) -> None:
    """A ceiling that says stop is not a request for a second opinion.

    Letting an approver override a declared block would turn every block into a prompt, which is the
    erosion that makes a team stop trusting the gate.
    """
    approver = FakeApprover(answer=ApprovalState.GRANTED)
    decision = build(tenant, approver).decide(BLOCKING)

    assert not decision.proceeds
    assert approver.requests == []
    assert not decision.escalation.asked


def test_a_call_that_fits_is_never_escalated(tenant: SyntheticTenant) -> None:
    approver = FakeApprover()
    decision = build(tenant, approver).decide(FITTING)
    assert decision.proceeds
    assert approver.requests == []


def test_observe_mode_never_escalates(tenant: SyntheticTenant) -> None:
    """Observe forwards everything, so there is nothing to ask about — and asking would summon a
    human for a call that was always going to proceed."""
    approver = FakeApprover()
    keeper = build(tenant, approver, mode=Mode.OBSERVE)
    assert keeper.decide(CONFIRMING).proceeds
    assert approver.requests == []


# ---------------------------------------------------------------------------- the tier boundary


def test_with_no_approver_a_confirm_stops_exactly_as_before(tenant: SyntheticTenant) -> None:
    """The free tier. This is the behaviour everything else must degrade to."""
    decision = build(tenant, approver=None).decide(CONFIRMING)
    assert not decision.proceeds
    assert not decision.escalation.asked


def test_an_unreachable_control_plane_behaves_exactly_like_the_free_tier(
    tenant: SyntheticTenant,
) -> None:
    """The property the whole commercial story rests on.

    Paying for approvals must add no availability risk to enforcement: a control plane that is down,
    absent or unpaid all mean the same thing, and that thing is what free already does.
    """
    free = build(tenant, approver=None).decide(CONFIRMING)
    broken = build(tenant, FakeApprover(unreachable=True)).decide(CONFIRMING)

    assert broken.proceeds == free.proceeds is False
    assert broken.verdict is free.verdict
    # Distinguishable in the record, though — "nobody was asked" is not "a person said no".
    assert broken.escalation.error == "control plane unreachable"
    assert broken.escalation.approval is None


def test_an_approver_can_only_ever_be_more_permissive(tenant: SyntheticTenant) -> None:
    """Whatever the control plane answers, it can never stop a call free would have allowed."""
    for call in (FITTING, CONFIRMING, BLOCKING):
        free = build(tenant, approver=None).decide(call)
        for answer in (None, ApprovalState.GRANTED, ApprovalState.DENIED, ApprovalState.EXPIRED):
            paid = build(tenant, FakeApprover(answer=answer)).decide(call)
            if free.proceeds:
                assert paid.proceeds, f"{call.tool} regressed with answer={answer}"


# ---------------------------------------------------------------------------- one gate, three seams


def _engine(tenant: SyntheticTenant) -> Engine:
    policy: Policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.ENFORCE})
    client = GraphClient(CRED, transport=tenant.transport())
    return Engine(policy=policy, resolvers=resolvers_for_client(client))


def test_a_grant_is_honoured_identically_on_all_three_seams(tenant: SyntheticTenant) -> None:
    """The reason the escalation lives in one place.

    Three copies of this protocol would drift, and the one that drifted would be the one that let a
    call through. So: the same granted approval, asked for over MCP, over the Claude Code hook and
    in-process, has to produce the same answer in all three.
    """
    from neti.adapters.claude_code import run_hook
    from neti.gateway.mcp import McpGateway
    from neti.preflight import Preflight

    forwarded: list[str] = []

    class Upstream:
        def send(self, message: dict[str, Any], session_id: str | None) -> dict[str, Any]:
            forwarded.append(message["params"]["name"])
            return {"jsonrpc": "2.0", "id": message["id"], "result": {"ok": True}}

    mcp = McpGateway(
        engine=_engine(tenant),
        upstream=Upstream(),
        approver=FakeApprover(answer=ApprovalState.GRANTED),
    )
    response = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "send_email", "arguments": {"to": "g-dept"}},
        }
    )
    assert response is not None
    assert "isError" not in response["result"]  # the tool actually ran
    assert forwarded == ["send_email"]

    hooked = run_hook(
        _engine(tenant),
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "send_email",
            "tool_input": {"to": "g-dept"},
        },
        approver=FakeApprover(answer=ApprovalState.GRANTED),
    )
    # A pass says nothing at all, which leaves the operator's own permission rules untouched.
    assert hooked == {}

    inproc = Preflight(
        engine=_engine(tenant), approver=FakeApprover(answer=ApprovalState.GRANTED)
    ).check("send_email", {"to": "g-dept"})
    assert inproc.proceeds
    assert inproc.approval_state == "granted"


def test_a_pending_approval_tells_the_model_to_retry(tenant: SyntheticTenant) -> None:
    """The sentence is load-bearing: an agent told only "denied" gives up or repeats itself."""
    from neti.gateway.mcp import McpGateway

    class Silent:
        def send(self, message: dict[str, Any], session_id: str | None) -> dict[str, Any] | None:
            raise AssertionError("a call awaiting approval must not reach the server")

    gateway = McpGateway(engine=_engine(tenant), upstream=Silent(), approver=FakeApprover())
    response = gateway.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "send_email", "arguments": {"to": "g-dept"}},
        }
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is True
    assert "pending" in result["content"][0]["text"]
    assert "Retry this exact call" in result["content"][0]["text"]
    assert result["_meta"]["neti"]["approval_state"] == "pending"
