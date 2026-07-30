"""The Terraform plan resolver.

Two things are being tested beyond arithmetic. That the resolver declines to guess when handed
something that is not a plan — a JSON document with no `resource_changes` must not read as "destroys
nothing" — and that the direction machinery actually earns its keep: a plan containing unknown
values is a lower bound, which can block but cannot allow.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from neti.config.policy import Policy
from neti.core.decide import decide_arg
from neti.core.types import Band, Ceiling, ProposedCall
from neti.core.units import Direction, Unit
from neti.core.verdict import Mode, ResolutionState, Verdict
from neti.engine import Engine
from neti.resolvers.base import ResolveContext
from neti.resolvers.terraform import TerraformPlanResolver, summarise_plan

CTX = ResolveContext()
RESOLVER = TerraformPlanResolver()


def plan(*changes: dict[str, Any], version: str = "1.9.5") -> str:
    return json.dumps(
        {
            "format_version": "1.2",
            "terraform_version": version,
            "resource_changes": list(changes),
        }
    )


def change(address: str, *actions: str, unknown: Any = None) -> dict[str, Any]:
    detail: dict[str, Any] = {"actions": list(actions)}
    if unknown is not None:
        detail["after_unknown"] = unknown
    return {"address": address, "change": detail}


# --------------------------------------------------------------- counting


def test_counts_destroys_and_replaces_but_not_creates() -> None:
    """A plan that stands up 200 resources is not the failure mode anyone fears."""
    res = RESOLVER.resolve(
        plan(
            change("aws_db_instance.main", "delete"),
            change("aws_ecs_cluster.main", "delete"),
            change("aws_lb.front", "delete", "create"),
            change("aws_s3_bucket.new", "create"),
            change("aws_s3_bucket.other", "create"),
            change("aws_iam_role.r", "update"),
            change("aws_vpc.v", "no-op"),
        ),
        CTX,
    )
    assert res.state is ResolutionState.RESOLVED
    assert res.magnitude == 3, "two destroys plus one replace"
    assert res.unit is Unit.RESOURCES
    assert res.breakdown == {"destroy": 2, "replace": 1, "create": 2, "update": 1, "no-op": 1}


def test_create_before_destroy_replacement_counts_the_same() -> None:
    """Terraform emits the two actions in either order; both destroy the original."""
    a, _ = summarise_plan(json.loads(plan(change("x", "delete", "create"))))
    b, _ = summarise_plan(json.loads(plan(change("x", "create", "delete"))))
    assert a["replace"] == b["replace"] == 1


def test_an_empty_plan_resolves_to_zero_rather_than_failing() -> None:
    """A genuinely empty plan is a real answer, distinct from an unreadable one."""
    res = RESOLVER.resolve(plan(), CTX)
    assert res.state is ResolutionState.RESOLVED
    assert res.magnitude == 0


def test_the_resolution_is_strongly_consistent_and_carries_no_staleness() -> None:
    """A local artifact, unlike every other resolver. The contract degrades correctly."""
    res = RESOLVER.resolve(plan(change("x", "delete")), CTX)
    assert res.consistency == "strong"
    assert res.provider_snapshot == "1.9.5"


# --------------------------------------------------------------- direction


def test_a_plan_without_unknowns_is_exact() -> None:
    res = RESOLVER.resolve(plan(change("x", "delete")), CTX)
    assert res.direction is Direction.EXACT


def test_a_plan_with_unknown_values_is_a_lower_bound() -> None:
    """hashicorp/terraform#27916: a plan can under-count when count derives from an unknown."""
    res = RESOLVER.resolve(
        plan(change("x", "delete"), change("y", "create", unknown={"id": True})), CTX
    )
    assert res.direction is Direction.LOWER_BOUND
    assert res.evidence["has_unknown_values"] is True


def test_a_lower_bound_under_the_ceiling_cannot_simply_be_allowed() -> None:
    """The direction machinery earning its keep.

    One destroy against a ceiling of ten looks fine — but the plan admits it does not know its own
    full expansion, so "under the ceiling" proves nothing and the declared on_unbounded applies.
    """
    ceiling = Ceiling(
        unit=Unit.RESOURCES,
        bands=(Band(above=10, verdict=Verdict.BLOCK),),
        on_unbounded=Verdict.CONFIRM,
    )
    res = RESOLVER.resolve(
        plan(change("x", "delete"), change("y", "create", unknown={"id": True})), CTX
    )
    decision = decide_arg("/plan", "…", ceiling, res)
    assert decision.verdict is Verdict.CONFIRM
    assert decision.rule == "on_unbounded:lower_bound"


def test_a_lower_bound_over_the_ceiling_blocks_soundly() -> None:
    """Measured over the ceiling means the truth is over it too — no caveat needed."""
    ceiling = Ceiling(unit=Unit.RESOURCES, bands=(Band(above=2, verdict=Verdict.BLOCK),))
    res = RESOLVER.resolve(
        plan(
            *[change(f"x{i}", "delete") for i in range(5)],
            change("y", "create", unknown={"id": True}),
        ),
        CTX,
    )
    decision = decide_arg("/plan", "…", ceiling, res)
    assert decision.verdict is Verdict.BLOCK
    assert not decision.over_block_possible, "a lower bound is sound to block on"


# --------------------------------------------------------------- refusing to guess


def test_json_that_is_not_a_plan_is_unresolved_not_zero() -> None:
    """The single most dangerous possible behaviour: reading as "this destroys nothing"."""
    res = RESOLVER.resolve(json.dumps({"hello": "world"}), CTX)
    assert res.state is ResolutionState.UNRESOLVED
    assert res.magnitude is None
    assert res.evidence["reason"] == "not_a_terraform_plan"


@pytest.mark.parametrize("bad", ["{not json", '{"a": ', "[]"])
def test_malformed_input_is_unresolved(bad: str) -> None:
    res = RESOLVER.resolve(bad, CTX)
    assert res.state is ResolutionState.UNRESOLVED
    assert res.magnitude is None


def test_a_missing_plan_file_is_unresolved_with_a_usable_hint() -> None:
    res = RESOLVER.resolve("/nonexistent/plan.json", CTX)
    assert res.state is ResolutionState.UNRESOLVED
    assert "unreadable" in res.evidence["reason"]
    assert "the same plan the agent is about to apply" in res.evidence["hint"]


def test_reachable_max_declines_rather_than_inventing_a_bound() -> None:
    """An invented number here would appear in the hour-one report as a capability claim."""
    res = RESOLVER.reachable_max(CTX)
    assert res.state is ResolutionState.UNRESOLVED
    assert "state" in res.evidence["reason"]


# --------------------------------------------------------------- from a file, end to end


def test_a_plan_on_disk_gates_a_real_call(tmp_path: Path) -> None:
    """The shape of the Claude Code incident: one apply, a fleet of destroys."""
    path = tmp_path / "plan.json"
    path.write_text(
        plan(
            change("aws_db_instance.production", "delete"),
            change("aws_db_snapshot.auto", "delete"),
            change("aws_ecs_cluster.main", "delete"),
            change("aws_lb.public", "delete"),
            change("aws_vpc.main", "delete"),
        ),
        encoding="utf-8",
    )

    policy = Policy.model_validate(
        {
            "mode": "enforce",
            "tools": {
                "terraform_apply": {
                    "gate": {
                        "/plan": {
                            "resolver": "terraform.destroy",
                            "bands": [
                                {"above": 0, "verdict": "confirm"},
                                {"above": 3, "verdict": "block"},
                            ],
                            "on_unresolved": "block",
                        }
                    }
                }
            },
        }
    )
    engine = Engine(policy=policy, resolvers={"terraform.destroy": RESOLVER}, ctx=CTX)
    result = engine.gate(ProposedCall(tool="terraform_apply", args={"plan": str(path)}))

    assert result.decision.verdict is Verdict.BLOCK
    assert not result.proceeds
    payload = engine.denial_payload(result)
    assert payload["resolved"] == 5
    assert payload["unit"] == "resources"
    assert payload["ceiling"] == 3

    cause = result.record.causes[0]
    assert cause["breakdown"]["destroy"] == 5
    assert cause["consistency"] == "strong"


def test_a_small_plan_proceeds(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_text(plan(change("aws_s3_bucket.new", "create")), encoding="utf-8")
    policy = Policy.model_validate(
        {
            "mode": "enforce",
            "tools": {
                "terraform_apply": {
                    "gate": {
                        "/plan": {
                            "resolver": "terraform.destroy",
                            "bands": [{"above": 3, "verdict": "block"}],
                        }
                    }
                }
            },
        }
    )
    engine = Engine(policy=policy, resolvers={"terraform.destroy": RESOLVER}, ctx=CTX)
    result = engine.gate(ProposedCall(tool="terraform_apply", args={"plan": str(path)}))
    assert result.decision.verdict is Verdict.ALLOW
    assert result.proceeds


def test_the_resolver_needs_no_credential(tmp_path: Path) -> None:
    """Unlike the directory resolvers, it is always available — which is why it is registered
    unconditionally rather than behind `build_entra_resolvers`."""
    engine = Engine(
        policy=Policy.model_validate({"mode": Mode.ENFORCE.name.lower(), "tools": {}}),
        resolvers={"terraform.destroy": TerraformPlanResolver()},
    )
    assert engine.gate(ProposedCall(tool="anything", args={})).decision.verdict is Verdict.ALLOW
