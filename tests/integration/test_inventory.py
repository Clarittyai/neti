"""`neti inventory` against the synthetic tenant, plus the policy loader.

The property under test is the one the adoption argument rests on: a useful finding with **zero
declared ceilings and zero recorded traffic**.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neti.config.policy import Policy, PolicyError, load_policy
from neti.core.types import ProposedCall
from neti.core.verdict import Mode, ResolutionState, Verdict
from neti.insight.inventory import build_inventory, format_inventory
from neti.resolvers.base import ResolveContext
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from neti.eval.synthetic import SyntheticTenant, default_tenant

CTX = ResolveContext(timeout_ms=800)
CRED = ClientCredential(tenant_id="t", client_id="c", client_secret="s")
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "entra.yaml"


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


def test_example_policy_loads(tmp_path: Path) -> None:
    """The shipped example must stay loadable — it is the first file every operator copies."""
    policy = load_policy(EXAMPLE)
    assert policy.mode is Mode.OBSERVE, "the shipped example must not default to enforce"
    assert policy.is_gated("remove_group_members")
    assert not policy.is_gated("read_group")
    assert policy.unknown_tool is Verdict.ALLOW

    gates = policy.gate_specs("remove_group_members")
    assert set(gates) == {"/group", "/group#apps"}
    # bands are stored descending, whatever order the operator wrote them in
    assert [b.above for b in gates["/group"].bands] == [200, 25]
    assert gates["/group"].on_unresolved is Verdict.BLOCK

    assert policy.session_budgets, "NC-01 mitigation must be present in the shipped example"
    recipients = next(r for r in policy.session_budgets if r.unit.value == "recipients")
    assert "send_email" in recipients.tools


def test_policy_digest_is_stable_and_sensitive(tmp_path: Path) -> None:
    """The digest pins which policy produced a verdict; without it, replay claims nothing."""
    a = load_policy(EXAMPLE)
    b = load_policy(EXAMPLE)
    assert a.digest() == b.digest()

    text = EXAMPLE.read_text(encoding="utf-8").replace("above: 200", "above: 201", 1)
    altered = tmp_path / "altered.yaml"
    altered.write_text(text, encoding="utf-8")
    assert load_policy(altered).digest() != a.digest()


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    """A typo must not silently produce an ungated tool."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: 1\ntools:\n  send_email:\n    gate:\n      /to:\n"
        "        resolver: entra.principals\n        bandz: []\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicyError):
        load_policy(bad)


def test_gate_key_must_be_a_pointer(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: 1\ntools:\n  send_email:\n    gate:\n      to:\n"
        "        resolver: entra.principals\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicyError):
        load_policy(bad)


def test_absent_argument_is_not_an_empty_one() -> None:
    """A gated parameter the agent never supplied is a call we cannot size."""
    policy = load_policy(EXAMPLE)
    targets = policy.targets(ProposedCall(tool="remove_group_members", args={}))
    assert {t[1] for t in targets} == {None}


def test_inventory_needs_no_ceilings_and_no_traffic(tenant: SyntheticTenant) -> None:
    policy = Policy.model_validate(
        {
            "tools": {
                # deliberately no bands at all — this is what an operator has on day one
                "remove_group_members": {"gate": {"/group": {"resolver": "entra.principals"}}},
                "revoke_app_access": {"gate": {"/group": {"resolver": "entra.apps"}}},
            }
        }
    )
    with GraphClient(CRED, transport=tenant.transport()) as client:
        rows = build_inventory(policy, resolvers_for_client(client), CTX)

    by_resolver = {r.resolver: r for r in rows}
    assert by_resolver["entra.principals"].reachable.magnitude == 52_400
    assert by_resolver["entra.apps"].reachable.magnitude == 214
    assert all(not r.has_ceiling for r in rows)

    report = format_inventory(rows)
    assert "52,400" in report
    assert "no ceiling declared" in report
    assert "2 of 2 gated parameters have no ceiling declared" in report
    # Inventory reports capability; it must never read as a measurement of a call.
    assert "UPPER BOUNDS" in report


def test_inventory_deduplicates_reachable_max_calls(tenant: SyntheticTenant) -> None:
    """A 50-tool policy must not make 50 identical calls to state one tenant-wide bound."""
    policy = Policy.model_validate(
        {
            "tools": {
                f"tool_{i}": {"gate": {"/group": {"resolver": "entra.principals"}}}
                for i in range(10)
            }
        }
    )
    with GraphClient(CRED, transport=tenant.transport()) as client:
        before = len(tenant.calls)
        rows = build_inventory(policy, resolvers_for_client(client), CTX)
    graph_calls = [c for c in tenant.calls[before:] if "token" not in c]
    assert len(rows) == 10
    assert len(graph_calls) == 1


def test_inventory_reports_a_missing_resolver_rather_than_crashing(
    tenant: SyntheticTenant,
) -> None:
    policy = Policy.model_validate(
        {"tools": {"t": {"gate": {"/g": {"resolver": "entra.nonexistent"}}}}}
    )
    with GraphClient(CRED, transport=tenant.transport()) as client:
        rows = build_inventory(policy, resolvers_for_client(client), CTX)
    assert rows[0].reachable.state is ResolutionState.UNRESOLVED
    assert "no resolver registered" in format_inventory(rows)


def test_inventory_flags_partial_coverage(tenant: SyntheticTenant) -> None:
    """A ceiling below the reachable maximum is coverage; confirm-only is not, and says so."""
    policy = Policy.model_validate(
        {
            "tools": {
                "capped": {
                    "gate": {
                        "/g": {
                            "resolver": "entra.principals",
                            "bands": [{"above": 200, "verdict": "block"}],
                        }
                    }
                },
                "confirm_only": {
                    "gate": {
                        "/g": {
                            "resolver": "entra.principals",
                            "bands": [{"above": 10, "verdict": "confirm"}],
                        }
                    }
                },
            }
        }
    )
    with GraphClient(CRED, transport=tenant.transport()) as client:
        rows = build_inventory(policy, resolvers_for_client(client), CTX)
    risks = {r.tool: r.risk for r in rows}
    assert "capped at 200 of 52,400" in risks["capped"]
    assert "confirm only" in risks["confirm_only"]
