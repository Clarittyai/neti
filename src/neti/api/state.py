"""The demo/live seam.

One argument decides whether the gate talks to a fixture or to Microsoft. Everything downstream —
`resolvers_for_client`, `Engine`, `decide`, `build_record` — is literally the same object in both
modes, which is the whole reason the demo is a proof rather than a mock: a viewer watching the
synthetic tenant is watching the code that would run against their own directory.

The mode is surfaced honestly and permanently. `neti.api` never pretends a fixture is a tenant.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from neti.config.policy import Policy, load_policy
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.synthetic import SyntheticTenant, default_tenant
from neti.resolvers.base import ResolveContext
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from neti.store.jsonl import JsonlSink, chain_head

__all__ = ["ConsoleState", "build_state"]

DEMO_CREDENTIAL = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")


@dataclass
class ConsoleState:
    """Everything one console process holds. Constructed once, at startup."""

    engine: Engine
    client: GraphClient
    policy: Policy
    sink: JsonlSink
    records_path: Path
    demo: bool
    tenant_label: str
    tenant: SyntheticTenant | None = None
    """The fixture, when in demo mode — the console shows its declared contents so a viewer can
    check the resolver's answers against the ground truth rather than taking them on trust."""

    connected: bool = False
    """A console starts disconnected even in demo mode, so the connect flow is a real flow rather
    than a decoration. Nothing resolves until it is true."""

    _rebuild: Any = field(default=None, repr=False)

    @property
    def mode(self) -> str:
        return "demo" if self.demo else "live"

    def as_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "connected": self.connected,
            "tenant": self.tenant_label,
            "policy_digest": self.policy.digest(),
            "policy_mode": self.policy.mode.name.lower(),
            "gated_tools": sorted(t for t in self.policy.tools if self.policy.gate_specs(t)),
            "records": str(self.records_path),
            # Ground truth, published on purpose. In demo mode the viewer can check every number the
            # resolver returns against what the fixture declares.
            "fixture": None
            if self.tenant is None
            else [
                {
                    "id": g.group_id,
                    "name": g.display_name,
                    "members": g.transitive_members,
                    "guests": g.guests,
                    "apps": g.app_assignments,
                    "kind": g.kind,
                }
                for g in sorted(self.tenant.groups.values(), key=lambda g: -g.transitive_members)
            ],
        }

    def set_mode(self, mode: Mode) -> None:
        """Swap the policy's mode, rebuilding the engine around it.

        `Policy` is frozen, so this replaces it rather than mutating it — which also means the
        policy digest changes, which is correct: a record must say which policy produced it, and
        observe and enforce are different policies. The chain continues across the swap.
        """
        self.policy = self.policy.model_copy(update={"mode": mode})
        self.engine = Engine(
            policy=self.policy,
            resolvers=self.engine.resolvers,
            ctx=self.engine.ctx,
            last_digest=self.engine.last_digest,
        )

    def close(self) -> None:
        self.sink.close()
        self.client.close()


def build_state(
    *,
    config: str | Path = "examples/entra.yaml",
    records: str | Path = "out/console.ndjson",
    demo: bool | None = None,
    timeout_ms: int = 800,
) -> ConsoleState:
    """Build the console's world.

    `demo` defaults to "whatever the environment can actually do": with no credentials there is
    nothing to talk to, so the fixture is the only honest choice, and the console must still come up
    — a demo that requires a tenant to start is not a demo.
    """
    policy = load_policy(config)
    records_path = Path(records)

    creds = {
        n: os.environ.get(n) for n in ("NETI_TENANT_ID", "NETI_CLIENT_ID", "NETI_CLIENT_SECRET")
    }
    have_creds = all(creds.values())
    if demo is None:
        demo = not have_creds

    tenant: SyntheticTenant | None = None
    if demo:
        tenant = default_tenant()
        client = GraphClient(DEMO_CREDENTIAL, transport=tenant.transport(), timeout_ms=timeout_ms)
        label = "Contoso (synthetic fixture)"
    else:
        if not have_creds:
            missing = ", ".join(n for n, v in creds.items() if not v)
            raise RuntimeError(f"live mode needs {missing}")
        client = GraphClient(
            ClientCredential(
                tenant_id=creds["NETI_TENANT_ID"] or "",
                client_id=creds["NETI_CLIENT_ID"] or "",
                client_secret=creds["NETI_CLIENT_SECRET"] or "",
            ),
            timeout_ms=timeout_ms,
        )
        label = creds["NETI_TENANT_ID"] or "live tenant"

    engine = Engine(
        policy=policy,
        resolvers=resolvers_for_client(client),
        ctx=ResolveContext(timeout_ms=timeout_ms),
        last_digest=chain_head(records_path),
    )

    return ConsoleState(
        engine=engine,
        client=client,
        policy=policy,
        sink=JsonlSink(records_path),
        records_path=records_path,
        demo=demo,
        tenant_label=label,
        tenant=tenant,
    )
