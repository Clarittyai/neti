"""The in-process seam, for agents that speak neither MCP nor a hook protocol.

A great deal of agent work today is a plain tool-calling loop: functions registered with the
Anthropic or OpenAI SDK, a LangChain `@tool`, a CrewAI action. There is no proxy anywhere near those
calls and no hook to install, so the only place a gate can sit is in the dispatch the author already
writes. Three lines, in the loop they already have:

    pf = Preflight.from_config("neti.yaml")

    for block in message.content:                       # an Anthropic tool-use loop
        if block.type == "tool_use":
            out = pf.dispatch(block.name, block.input, lambda: TOOLS[block.name](**block.input))

`dispatch` returns the tool's own return value when the call fits, and the denial *sentence* when it
does not — because the caller's next move is to hand that string back to the model as the tool
result, and a gate that raised there would make every author write the same try/except. `check` and
`guard` are the other two shapes the same decision comes in.

This is deliberately thin. It constructs nothing the other transports do not, writes to the same
record chain, and produces byte-identical denial text — an agent must not be able to tell which
seam stopped it, or the product has three behaviours instead of one.

Being in-process is also the honest weakness, and it is worth stating rather than discovering: an
author who forgets to route one tool through here has no gate on that tool, and nothing detects the
omission. The MCP and hook paths cannot be forgotten that way, which is why they come first.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from neti.approvals import Approver
from neti.core.types import ProposedCall
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.gatekeeper import Decision, Gatekeeper
from neti.gateway.mcp import explain_denial
from neti.store.jsonl import JsonlSink, chain_head

__all__ = ["Blocked", "Preflight", "Verdict"]

T = TypeVar("T")


class Blocked(Exception):
    """Raised by `guard` when a call exceeds a declared ceiling.

    Carries the same sentence and the same numbers the MCP and hook paths return, so a framework
    that turns exceptions into tool results produces exactly what the other seams would have.
    """

    def __init__(self, message: str, payload: dict[str, Any]) -> None:
        super().__init__(message)
        self.message = message
        self.payload = payload


@dataclass(frozen=True)
class Verdict:
    """What one gated call resolved to."""

    proceeds: bool
    verdict: str
    message: str
    """Empty when the call fits. Otherwise the sentence to hand back to the model."""

    rule: str
    """What decided — a per-argument ceiling or a `session_budget:` total. The remedies are
    different enough to be worth distinguishing: narrow this call, versus start a new session."""

    payload: dict[str, Any]
    decision_id: str

    approval_id: str | None = None
    """Set when a human was asked. `check` never blocks waiting for one — `dispatch` and `guard` do,
    because they are the shapes that own the call's execution."""

    approval_state: str | None = None


@dataclass
class Preflight:
    """A gate you call yourself."""

    engine: Engine
    sink: JsonlSink | None = None
    approver: Approver | None = None
    """A control plane, when there is one. Without it a `CONFIRM` stops the call, which is the free
    tier's behaviour and the one this degrades to whenever the server cannot be reached."""

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_config(
        cls,
        config: str | Path = "neti.yaml",
        *,
        records: str | Path | None = "out/decisions.ndjson",
        mode: str | None = None,
        timeout_ms: int = 800,
    ) -> Preflight:
        """Build from a policy file, and from the environment's Entra credentials if it needs them.

        **Only if it needs them.** This asked for `NETI_TENANT_ID`, `NETI_CLIENT_ID` and
        `NETI_CLIENT_SECRET` unconditionally, so the shipped `examples/coding-agent.yaml` — every
        gate `fs.paths`, no credential required anywhere in it — raised `ResolverError` on the one
        seam the README tells you to call from your own tool loop. The CLI had already been fixed
        for exactly this and this had not, because the two build their resolvers by different
        routes and only one of them was looked at.
        """
        from neti.config.policy import load_policy
        from neti.resolvers.base import ResolveContext
        from neti.resolvers.graph_client import ClientCredential, GraphClient
        from neti.resolvers.registry import build_entra_resolvers, resolvers_for_client

        policy = load_policy(str(config))
        if mode is not None:
            policy = policy.model_copy(update={"mode": Mode[mode.upper()]})
        if policy.binds_entra():
            resolvers, _client = build_entra_resolvers(
                timeout_ms=timeout_ms, providers=policy.providers
            )
        else:
            # The registry is still built whole: the entra resolvers are present but hold a blank
            # credential, so a policy that somehow named one fails loudly on its first token fetch
            # rather than being absent and tripping `_check_resolvers_exist` at construction.
            # Nothing here touches the network — `GraphClient` acquires its token lazily.
            blank = ClientCredential(tenant_id="", client_id="", client_secret="")
            client = GraphClient(blank, timeout_ms=timeout_ms)
            resolvers = resolvers_for_client(client, policy.providers)
        return cls._assemble(policy, resolvers, records, timeout_ms, ResolveContext)

    @classmethod
    def demo(
        cls,
        config: str | Path = "examples/entra.yaml",
        *,
        records: str | Path | None = None,
        mode: str | None = "enforce",
        timeout_ms: int = 800,
    ) -> Preflight:
        """Build against the synthetic tenant, so this can be tried with no credentials."""
        from neti.config.policy import load_policy
        from neti.eval.synthetic import default_tenant
        from neti.resolvers.base import ResolveContext
        from neti.resolvers.graph_client import ClientCredential, GraphClient
        from neti.resolvers.registry import resolvers_for_client

        policy = load_policy(str(config))
        if mode is not None:
            policy = policy.model_copy(update={"mode": Mode[mode.upper()]})
        credential = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")
        client = GraphClient(
            credential, transport=default_tenant().transport(), timeout_ms=timeout_ms
        )
        return cls._assemble(
            policy,
            resolvers_for_client(client, policy.providers),
            records,
            timeout_ms,
            ResolveContext,
        )

    @classmethod
    def _assemble(
        cls,
        policy: Any,
        resolvers: Any,
        records: str | Path | None,
        timeout_ms: int,
        resolve_context: Any,
    ) -> Preflight:
        engine = Engine(
            policy=policy,
            resolvers=resolvers,
            ctx=resolve_context(timeout_ms=timeout_ms),
            # Continue the file's existing chain; a fresh engine that starts over writes a break
            # that `neti verify` correctly reports as one.
            last_digest=chain_head(str(records)) if records else None,
        )
        return cls(engine=engine, sink=JsonlSink(str(records)) if records else None)

    # ------------------------------------------------------------------ the three shapes

    def check(self, tool: str, args: dict[str, Any], *, session_id: str | None = None) -> Verdict:
        """Resolve and decide. Nothing is executed and nothing is raised."""
        decision = self._gatekeeper().decide(
            ProposedCall(tool=tool, args=args, session_id=session_id)
        )
        return self._verdict(decision)

    def dispatch(
        self,
        tool: str,
        args: dict[str, Any],
        call: Callable[[], T],
        *,
        session_id: str | None = None,
    ) -> T | str:
        """Run `call` if the gate allows it, or return the denial sentence if it does not.

        The denial comes back as a value rather than an exception because the caller's next line is
        almost always "append this to the tool results", and the model reading a specific number is
        what causes it to retry with a narrower scope instead of giving up or repeating itself.
        """
        verdict = self.check(tool, args, session_id=session_id)
        if not verdict.proceeds:
            return verdict.message
        return call()

    def guard(self, fn: Callable[..., T]) -> Callable[..., T]:
        """Decorate a tool function. Raises `Blocked` when the call exceeds a ceiling.

        The tool's own name is the policy key, and only keyword arguments are gated: a JSON pointer
        into a positional argument would depend on the signature's order, which is exactly the kind
        of thing that silently stops matching after a refactor.
        """

        @functools.wraps(fn)
        def wrapper(*a: Any, **kw: Any) -> T:
            verdict = self.check(fn.__name__, kw)
            if not verdict.proceeds:
                raise Blocked(verdict.message, verdict.payload)
            return fn(*a, **kw)

        return wrapper

    def close(self) -> None:
        if self.sink is not None:
            self.sink.close()

    # ------------------------------------------------------------------ internals

    def _gatekeeper(self) -> Gatekeeper:
        return Gatekeeper(engine=self.engine, sink=self.sink, approver=self.approver)

    def _verdict(self, decision: Decision) -> Verdict:
        result = decision.result
        payload = self.engine.denial_payload(result)
        approval = decision.escalation.approval
        return Verdict(
            proceeds=decision.proceeds,
            verdict=result.decision.verdict.name.lower(),
            # A call a human approved is not a denial, so it carries no denial sentence — the
            # verdict stays `confirm` because that is what the policy said, and `proceeds` is what
            # changed. Conflating the two would erase the fact that a person was asked.
            message="" if decision.proceeds else explain_denial(result, payload),
            rule=result.decision.rule,
            payload=payload,
            decision_id=result.record.decision_id,
            approval_id=approval.id if approval else None,
            approval_state=str(approval.state) if approval else None,
        )
