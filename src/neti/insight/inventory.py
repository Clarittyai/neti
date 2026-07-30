"""`neti inventory` — the hour-one artifact.

A finding with **no traffic, no declared ceilings and no integration effort**: for every gated
parameter, the largest magnitude the credential behind it could reach in one call.

> Your agent holds a token that can, in one call, remove 41,203 people from a group.

That sentence is why this ships in Phase 1, before the gateway exists. It is also the honest form of
the borrowed 8-million-files anecdote: a capability statement about *this* tenant, which is stronger
evidence than someone else's incident, and which nobody can dispute.

The second column that matters is the one people skip: **which gated parameters have no ceiling
declared**. A gate that resolves but never blocks is observe-mode value, not protection, and the
inventory says so rather than letting a configured-looking policy imply coverage.
"""

from __future__ import annotations

from dataclasses import dataclass

from neti.config.policy import GateSpec, Policy
from neti.core.types import Resolution
from neti.core.units import Unit
from neti.core.verdict import ResolutionState
from neti.resolvers.base import ResolveContext, Resolver

__all__ = ["InventoryRow", "build_inventory", "format_inventory"]


@dataclass
class InventoryRow:
    tool: str
    pointer: str
    resolver: str
    spec: GateSpec
    reachable: Resolution

    @property
    def has_ceiling(self) -> bool:
        return self.spec.has_ceiling

    @property
    def block_at(self) -> int | None:
        blocking = [b.above for b in self.spec.bands if b.verdict.name == "BLOCK"]
        return min(blocking) if blocking else None

    @property
    def risk(self) -> str:
        """The sentence that makes the row land."""
        if self.reachable.state is not ResolutionState.RESOLVED:
            return f"unresolved: {self.reachable.evidence.get('reason', 'unknown')}"
        magnitude = self.reachable.magnitude or 0
        unit = self.reachable.unit.value
        if not self.has_ceiling:
            return f"no ceiling declared — up to {magnitude:,} {unit} in one call"
        ceiling = self.block_at
        if ceiling is None:
            return f"confirm only — up to {magnitude:,} {unit} can still proceed"
        if magnitude > ceiling:
            return f"capped at {ceiling:,} of {magnitude:,} reachable {unit}"
        return f"fully covered ({magnitude:,} {unit} reachable, blocks above {ceiling:,})"


def build_inventory(
    policy: Policy,
    resolvers: dict[str, Resolver],
    ctx: ResolveContext,
) -> list[InventoryRow]:
    """One `reachable_max` per distinct resolver, reused across every parameter that binds it.

    Deduplicated on purpose: the tenant-wide bound does not depend on which tool asks, and a
    50-tool policy should not make 50 identical directory calls to say so.
    """
    cache: dict[str, Resolution] = {}
    rows: list[InventoryRow] = []

    for tool in sorted(policy.tools):
        for pointer, spec in policy.gate_specs(tool).items():
            resolver = resolvers.get(spec.resolver)
            if resolver is None:
                rows.append(
                    InventoryRow(
                        tool=tool,
                        pointer=pointer,
                        resolver=spec.resolver,
                        spec=spec,
                        reachable=Resolution.unresolved(
                            spec.unit or Unit.OBJECTS,
                            reason=f"no resolver registered named {spec.resolver!r}",
                        ),
                    )
                )
                continue
            if spec.resolver not in cache:
                cache[spec.resolver] = resolver.reachable_max(ctx)
            rows.append(
                InventoryRow(
                    tool=tool,
                    pointer=pointer,
                    resolver=spec.resolver,
                    spec=spec,
                    reachable=cache[spec.resolver],
                )
            )
    return rows


def format_inventory(rows: list[InventoryRow]) -> str:
    if not rows:
        return "No gated parameters declared. Nothing to inventory — see SCOPE.md NC-09."

    widths = (
        max(len(r.tool) for r in rows),
        max(len(r.pointer) for r in rows),
        max(len(r.resolver) for r in rows),
    )
    out = [
        f"{'tool':<{widths[0]}}  {'param':<{widths[1]}}  {'resolver':<{widths[2]}}  "
        f"{'max reachable':>13}  risk",
        "-" * (widths[0] + widths[1] + widths[2] + 40),
    ]
    for row in sorted(rows, key=lambda r: -(r.reachable.magnitude or 0)):
        magnitude = f"{row.reachable.magnitude:,}" if row.reachable.magnitude is not None else "?"
        out.append(
            f"{row.tool:<{widths[0]}}  {row.pointer:<{widths[1]}}  {row.resolver:<{widths[2]}}  "
            f"{magnitude:>13}  {row.risk}"
        )

    ungated = [r for r in rows if not r.has_ceiling]
    if ungated:
        out.append("")
        out.append(
            f"{len(ungated)} of {len(rows)} gated parameters have no ceiling declared. "
            "They resolve and record, but they cannot block."
        )
        out.append("Run `neti report` after a week of observe mode, then `neti propose`.")

    out.append("")
    out.append(
        "Reachable maxima are UPPER BOUNDS on tenant capability, not measurements of any call. "
        "They are reporting only and never gate a decision."
    )
    return "\n".join(out)
