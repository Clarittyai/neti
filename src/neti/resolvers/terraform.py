"""Size a Terraform plan before it is applied.

This resolver exists because the largest entry in the incident corpus is one `terraform destroy`
that took 1,943,200 rows with it, and because it exercises three properties of the model that the
directory resolvers never touch:

**It reads a local artifact, not a provider.** No round trip, no throttling, no eventual
consistency. `consistency` is `strong` and there is no staleness bound to declare — the plan file is
the plan file. That is worth noticing: the resolver contract was designed against a
network-and-eventual-consistency case and it degrades correctly to a local-and-exact one.

**Its direction is conditional.** A plan enumerates exactly what Terraform intends to do, which
would make it EXACT — except that a plan can under-count when a resource's `count` or `for_each`
derives from a value that is not known until apply (hashicorp/terraform#27916). When the plan
contains unknowns the true destroy count can only be *higher*, so the resolution is a LOWER_BOUND.
That is precisely the case the decision procedure's direction rules were written for: a lower bound
can soundly block (measured over the ceiling means the truth is too) but cannot soundly allow
(measured under it proves nothing), so an under-ceiling plan with unknowns escalates to the declared
`on_unbounded` verdict rather than sailing through.

**The magnitude is the irreversible subset.** Creates are not counted. A plan that stands up two
hundred resources is not the failure mode anyone is frightened of; a plan that destroys eleven is.
Destroys and replaces are counted together because a replace destroys the original, and `create`
and `update` are exposed in the breakdown so an operator can band them separately if they want to.

**Honest deployment limitation.** The gate must be able to read the same plan artifact the agent is
about to apply — either the argument carries the plan JSON inline, or it is a path the gate process
can open. Where neither holds, this resolver returns UNRESOLVED rather than guessing, and the
declared `on_unresolved` verdict applies.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from neti.core.types import Resolution
from neti.core.units import Direction, Unit
from neti.resolvers.base import ResolveContext

__all__ = ["TerraformPlanResolver", "summarise_plan"]

# Terraform represents a replace as both actions in one change, in either order depending on
# create_before_destroy. Both destroy the original, so both count.
_DESTRUCTIVE = ({"delete"}, {"delete", "create"})


def summarise_plan(plan: dict[str, Any]) -> tuple[dict[str, int], bool]:
    """Count actions by kind, and report whether the plan contains unknown values.

    Returns `(counts, has_unknowns)`. `counts` always carries every key, so a caller banding on
    `breakdown["create"]` does not have to distinguish "no creates" from "key absent".
    """
    counts = {"destroy": 0, "replace": 0, "create": 0, "update": 0, "no-op": 0}
    has_unknowns = False

    for change in plan.get("resource_changes") or []:
        detail = change.get("change") or {}
        actions = set(detail.get("actions") or [])

        if detail.get("after_unknown"):
            # Not itself a miscount — but unknown values are how a plan's own expansion becomes
            # uncertain, so their presence downgrades the whole resolution to a lower bound.
            has_unknowns = True

        if actions == {"delete", "create"}:
            counts["replace"] += 1
        elif actions == {"delete"}:
            counts["destroy"] += 1
        elif actions == {"create"}:
            counts["create"] += 1
        elif actions == {"update"}:
            counts["update"] += 1
        else:
            counts["no-op"] += 1

    return counts, has_unknowns


class TerraformPlanResolver:
    """Counts the resources a plan will destroy or replace.

    The target is either the plan JSON itself (as produced by `terraform show -json`) or a path to
    a file containing it.
    """

    unit: ClassVar[Unit] = Unit.RESOURCES
    # Every action `summarise_plan` counts. `destroy` and `replace` make the magnitude; the others
    # are here so an operator can band them separately, and naming them is what lets the Engine
    # refuse a `breakdown_bands` key that would never have fired.
    breakdown_keys: ClassVar[frozenset[str]] = frozenset(
        {"destroy", "replace", "create", "update", "no-op"}
    )

    def resolve(self, target: str, ctx: ResolveContext) -> Resolution:
        plan, error = _load(target)
        if plan is None:
            return Resolution.unresolved(
                self.unit,
                reason=error or "plan_unreadable",
                evidence={
                    "hint": (
                        "the gate must be able to read the same plan the agent is about to apply: "
                        "pass `terraform show -json` output inline, or a path this process can open"
                    )
                },
            )

        if "resource_changes" not in plan:
            # A JSON document that is not a plan. Guessing zero here would be the single most
            # dangerous possible behaviour: it reads as "this plan destroys nothing".
            return Resolution.unresolved(
                self.unit,
                reason="not_a_terraform_plan",
                evidence={"keys": sorted(plan)[:10]},
            )

        counts, has_unknowns = summarise_plan(plan)
        destructive = counts["destroy"] + counts["replace"]

        return Resolution.resolved(
            self.unit,
            destructive,
            direction=Direction.LOWER_BOUND if has_unknowns else Direction.EXACT,
            resolved_at=datetime.now(UTC),
            # A local file, read once. Nothing to be stale against.
            consistency="strong",
            provider_snapshot=str(plan.get("terraform_version") or "") or None,
            breakdown=counts,
            evidence={
                "format_version": plan.get("format_version"),
                "terraform_version": plan.get("terraform_version"),
                "has_unknown_values": has_unknowns,
                "counted": "destroy + replace (creates and updates are reversible)",
            },
        )

    def reachable_max(self, ctx: ResolveContext) -> Resolution:
        """Unknowable without reading state, and reading state is not this resolver's job.

        `neti inventory` prints the reason rather than a number. An invented bound here would be
        worse than an absent one: it would appear in the hour-one report as a capability claim.
        """
        return Resolution.unresolved(
            self.unit,
            reason="reachable_max_requires_terraform_state",
            evidence={
                "hint": "the largest possible destroy is the size of the state, which the gate "
                "does not read"
            },
        )


def _load(target: str) -> tuple[dict[str, Any] | None, str | None]:
    stripped = target.strip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            return None, f"plan_json_invalid: {exc}"
        return (parsed, None) if isinstance(parsed, dict) else (None, "plan_json_not_an_object")

    path = Path(stripped)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"plan_file_unreadable: {exc.strerror or exc}"
    except json.JSONDecodeError as exc:
        return None, f"plan_file_invalid_json: {exc}"
    return (parsed, None) if isinstance(parsed, dict) else (None, "plan_json_not_an_object")
