"""The decision. This module is DECISION.md in code; keep them in step.

Pure by construction: no I/O, no clock, no environment, no randomness. Resolutions arrive as input
because a resolver already did the I/O. That split is what makes a stored decision replayable, and
it is enforced by a test that walks this module's import graph.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from neti.core.types import (
    ArgDecision,
    Band,
    Breach,
    BudgetDecision,
    Ceiling,
    Decision,
    ProposedCall,
    Resolution,
)
from neti.core.units import Direction, may_allow, may_block
from neti.core.verdict import Mode, ResolutionState, Verdict, join_all

__all__ = ["decide", "decide_arg", "worst_tripped_band"]


def worst_tripped_band(magnitude: int, bands: tuple[Band, ...]) -> Band | None:
    """The most severe band this magnitude breaches, independent of input order.

    Deliberately order-independent rather than "first match in a sorted tuple". An earlier version
    walked the tuple and returned the first breach, which meant an unsorted band list silently
    reported the *mildest* applicable verdict — a session budget that should have blocked returned
    CONFIRM instead. Selecting by severity makes the sort an optimisation rather than a correctness
    requirement, so the bug cannot come back through a new caller that forgets to sort.

    Ties on severity are broken by the highest breached threshold, so the record cites the tightest
    ceiling the magnitude actually cleared.
    """
    breached = [b for b in bands if magnitude > b.above]
    if not breached:
        return None
    return max(breached, key=lambda b: (b.verdict, b.above))


def decide_arg(
    pointer: str,
    target: str | None,
    ceiling: Ceiling,
    resolution: Resolution,
    *,
    escaped: bool = False,
) -> ArgDecision:
    """Verdict for one gated argument.

    Four branches, in order. Every one of them fails closed.
    """
    # 1. We do not know the magnitude. Ignorance is never an implicit allow.
    if resolution.state is not ResolutionState.RESOLVED:
        # Not knowing has two shapes, and treating them alike is how a deletion passes in silence.
        # A resolver may say it recognised the target as destructive without being able to size it —
        # `cat list.txt | xargs rm` — and that is a different fact from `npm test`, which is simply
        # not a deletion. The operator declares what each one costs; nothing is inferred here.
        risky = bool(resolution.evidence.get("destructive"))
        if risky and ceiling.on_unsized_risk is not None:
            return ArgDecision(
                pointer=pointer,
                target=target,
                verdict=ceiling.on_unsized_risk,
                resolution=resolution,
                outside_root=escaped,
                rule="on_unsized_risk:destructive_but_unsizeable",
            )
        return ArgDecision(
            pointer=pointer,
            target=target,
            verdict=ceiling.on_unresolved,
            resolution=resolution,
            outside_root=escaped,
            rule=f"on_unresolved:{resolution.state.name.lower()}",
        )

    magnitude = resolution.magnitude
    assert magnitude is not None  # guaranteed by Resolution's validator

    # 2. Collect EVERY breach — the total and each named sub-count — not just the deciding one.
    #    Recording only the winner loses real evidence: "500 recipients, of whom 480 external"
    #    breaches both ceilings, and an auditor who sees only the external one cannot tell that the
    #    total was also over. The deciding breach is the most severe; the rest still get recorded.
    breaches = _all_breaches(magnitude, resolution.breakdown, ceiling)

    if breaches:
        worst = max(breaches, key=lambda b: (b.verdict, b.above, b.source))
        # Sound only if the true value cannot be *smaller* than measured. When it can be (an upper
        # bound) the restriction still stands — over-blocking is the acceptable error for a safety
        # gate, and it is what BigQuery's `maximum_bytes_billed` does — but it is recorded so the
        # friction metric can count how often it happens.
        unsound = not may_block(resolution.direction)
        return ArgDecision(
            pointer=pointer,
            target=target,
            verdict=worst.verdict,
            resolution=resolution,
            outside_root=escaped,
            rule=f"{worst.source}>{worst.above}" + ("+upper_bound" if unsound else ""),
            over_block_possible=unsound,
            tripped=Band(above=worst.above, verdict=worst.verdict),
            breaches=breaches,
        )

    # 3. Under every band. Only conclusive if the true value cannot be larger than what we measured.
    if not may_allow(resolution.direction):
        return ArgDecision(
            pointer=pointer,
            target=target,
            verdict=ceiling.on_unbounded,
            resolution=resolution,
            outside_root=escaped,
            rule=f"on_unbounded:{resolution.direction.value}",
        )

    return ArgDecision(
        pointer=pointer,
        target=target,
        verdict=Verdict.ALLOW,
        resolution=resolution,
        outside_root=escaped,
        rule="under_all_bands",
    )


def _all_breaches(
    magnitude: int,
    breakdown: Mapping[str, int],
    ceiling: Ceiling,
) -> tuple[Breach, ...]:
    """Every ceiling this resolution breaches, in a deterministic order.

    Sorted by source name so the record is byte-stable regardless of dict iteration order.
    """
    found: list[Breach] = []

    band = worst_tripped_band(magnitude, ceiling.bands)
    if band is not None:
        found.append(
            Breach(source="magnitude", observed=magnitude, above=band.above, verdict=band.verdict)
        )

    for key in sorted(ceiling.breakdown_bands):
        value = breakdown.get(key)
        if value is None:
            continue
        sub = worst_tripped_band(value, ceiling.breakdown_bands[key])
        if sub is not None:
            found.append(
                Breach(
                    source=f"breakdown:{key}",
                    observed=value,
                    above=sub.above,
                    verdict=sub.verdict,
                )
            )

    return tuple(sorted(found, key=lambda b: b.source))


def decide(
    call: ProposedCall,
    gated: tuple[tuple[str, str | None, Ceiling], ...],
    resolutions: Mapping[str, Resolution],
    *,
    mode: Mode = Mode.OBSERVE,
    budget: BudgetDecision | None = None,
    sensitive: tuple[Any, ...] = (),
    outside_root: Verdict | None = None,
    escaped: tuple[str, ...] = (),
    extra_targets: Mapping[str, tuple[str, ...]] = MappingProxyType({}),
) -> Decision:
    """Combine per-argument verdicts and any session budget by JOIN.

    `gated` is `(pointer, target, ceiling)` triples the policy produced for this call; an empty
    tuple means the tool is not gated, which is ALLOW and not a denial — an ungated tool is out of
    scope (SCOPE.md NC-09), and failing closed on everything undeclared would make the gate
    unusable on its first day.
    """
    if not gated:
        return Decision(
            verdict=Verdict.ALLOW,
            tool=call.tool,
            mode_applied=mode.name.lower(),
            rule="tool_not_gated",
        )

    args = tuple(
        decide_arg(
            pointer,
            target,
            ceiling,
            _lookup(resolutions, pointer, ceiling),
            escaped=pointer in escaped,
        )
        for pointer, target, ceiling in gated
    )
    verdicts = [a.verdict for a in args]

    # The second axis. A target can be dangerous because of *what it is* rather than how much of it
    # there is — `.env` is one object and under every ceiling anybody would write. Joined rather
    # than substituted, so it raises a verdict and never lowers one: getting a rule wrong here costs
    # a confirmation, never a silent allow.
    # Every target the pointer names, not only the argument itself. A shell command's argument list
    # holds paths — `cat .env | base64` is one call whose target string is a command, and the thing
    # worth stopping is inside it. The resolver surfaces them; nothing is measured here.
    hits = tuple(
        (pointer, rule)
        for pointer, target, _ in gated
        for candidate in ((target,) if target else ()) + tuple(extra_targets.get(pointer, ()))
        for rule in (_sensitive_hit(candidate, sensitive),)
        if rule is not None
    )
    verdicts.extend(rule.verdict for _, rule in hits)

    # And where the target *is*, which no ceiling and no scan of the project can reach.
    #
    # Consumed, never measured. Deciding whether a path escapes means resolving symlinks, which is
    # I/O, and `neti.core` performs none by invariant — a decision that re-read the filesystem would
    # answer differently tomorrow and replay would stop reproducing. The engine measures it and
    # passes the fact, exactly as it passes a magnitude.
    escapes = tuple(escaped) if outside_root is not None else ()
    if escapes and outside_root is not None:
        verdicts.append(outside_root)
    if budget is not None:
        verdicts.append(budget.verdict)
    worst = join_all(verdicts)

    return Decision(
        verdict=worst,
        args=args,
        budget=budget,
        sensitive=tuple(
            {"pointer": p, "match": r.match, "verdict": r.verdict.name.lower(), "why": r.why}
            for p, r in hits
        ),
        tool=call.tool,
        mode_applied=mode.name.lower(),
        rule=(
            f"{escapes[0]}:outside_root"
            if escapes and outside_root is worst
            else _sensitive_rule(hits, worst) or _dominant_rule(args, budget, worst)
        ),
    )


def _sensitive_hit(target: str, rules: tuple[Any, ...]) -> Any:
    """The first declared rule this target matches. First, not worst: the operator wrote them in an
    order, and a list read top-down is one they can reason about."""
    from neti.core.globs import matches

    for rule in rules:
        if matches(target, (rule.match,)) is not None:
            return rule
    return None


def _sensitive_rule(hits: tuple[tuple[str, Any], ...], worst: Verdict) -> str:
    """Name the sensitivity rule in the record, but only when it is what decided the call."""
    for pointer, rule in hits:
        if rule.verdict is worst:
            return f"{pointer}:sensitive:{rule.match}"
    return ""


def _lookup(resolutions: Mapping[str, Resolution], pointer: str, ceiling: Ceiling) -> Resolution:
    """A gated pointer with no resolution is a missing measurement, not an absent constraint.

    This is the path taken when the caller failed to resolve at all — a bug, a crash, or a resolver
    that was never registered. It must not silently allow.
    """
    found = resolutions.get(pointer)
    if found is None:
        return Resolution.unresolved(ceiling.unit, reason="no_resolution_supplied")
    return found


def _dominant_rule(
    args: tuple[ArgDecision, ...], budget: BudgetDecision | None, worst: Verdict
) -> str:
    """Which component to credit for the verdict.

    Per-argument causes win ties over the session budget, deliberately. Both can breach at the same
    severity on one call, and the remedies differ: a per-argument breach means "narrow this call",
    a budget breach means "this call was fine, the session total is not". Crediting the budget on a
    tie produced a denial that told the agent its scope was acceptable when the scope was in fact
    ten times the ceiling. The more specific cause is the more actionable one.
    """
    for arg in args:
        if arg.verdict == worst:
            return f"{arg.pointer}:{arg.rule}"
    if budget is not None and budget.verdict == worst and worst > Verdict.ALLOW:
        return f"session_budget:{budget.rule}"
    return "under_all_bands"


# Guard against the most likely future regression: a direction added to the enum without a decision
# about which side of the soundness argument it falls on.
_KNOWN_DIRECTIONS = {Direction.EXACT, Direction.UPPER_BOUND, Direction.LOWER_BOUND}
assert set(Direction) == _KNOWN_DIRECTIONS, (
    "a new Direction was added; decide_arg's soundness branches must be updated deliberately"
)
