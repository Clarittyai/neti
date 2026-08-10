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
    # Rules that name an *operation* rather than a target, evaluated before the not-gated shortcut
    # because they do not need a target — or a resolver, or a magnitude — to mean something.
    # `delete_repository` is irreversible whatever it is pointed at.
    #
    # This is a deliberate, narrow exception to NC-09. "An ungated tool is out of scope" means a
    # tool *nobody mentioned*; one named in `sensitive:` has been mentioned, in the file that
    # decides. Without the exception the only way to require a human for an unsizeable operation was
    # to invent a resolver binding for it, and where none fitted there was no way at all.
    tool_hits = tuple(
        ("", rule) for rule in sensitive if not rule.match and _applies_to(call.tool, rule)
    )

    if not gated and not tool_hits:
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
    hits = tool_hits + _target_hits(call.tool, gated, sensitive, extra_targets)
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
            {
                "pointer": p,
                "match": r.match or f"tool:{call.tool}",
                "verdict": r.verdict.name.lower(),
                "why": r.why,
            }
            for p, r in hits
        ),
        tool=call.tool,
        mode_applied=mode.name.lower(),
        rule=(
            f"{escapes[0]}:outside_root"
            if escapes and outside_root is worst
            else _sensitive_rule(call.tool, hits, worst) or _dominant_rule(args, budget, worst)
        ),
    )


def _target_hits(
    tool: str,
    gated: tuple[tuple[str, str | None, Ceiling], ...],
    sensitive: tuple[Any, ...],
    extra_targets: Mapping[str, tuple[str, ...]],
) -> tuple[tuple[str, Any], ...]:
    """Every `(pointer, rule)` pair this call trips, each recorded once.

    One rule, one entry per pointer. A shell command surfaces several paths, and a rule matching two
    of them is still one reason rather than two — the record should read as evidence, not as a tally
    of how many targets happened to trip the same glob.
    """
    found: list[tuple[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for pointer, target, _ in gated:
        candidates = ((target,) if target else ()) + tuple(extra_targets.get(pointer, ()))
        for candidate in candidates:
            for rule in _sensitive_hits(tool, candidate, sensitive):
                key = (pointer, id(rule))
                if key in seen:
                    continue
                seen.add(key)
                found.append((pointer, rule))
    return tuple(found)


def _applies_to(tool: str, rule: Any) -> bool:
    """Does this rule cover the tool being called?

    An empty `tools` covers everything, which is what every rule written before this did and what
    most rules still want. Glob-matched otherwise, so `mcp__github__delete_*` is one line rather
    than a list somebody has to remember to extend.
    """
    from neti.core.globs import matches

    declared = tuple(getattr(rule, "tools", ()) or ())
    return not declared or matches(tool, declared) is not None


def _sensitive_hits(tool: str, target: str, rules: tuple[Any, ...]) -> tuple[Any, ...]:
    """**Every** declared rule this call matches, in the order they were written.

    It used to return only the first, and that was a real under-enforcement bug the moment rules
    could be scoped to tools. Written the way anybody would write them —

        - { match: "**/.env*", verdict: confirm, why: credentials live here }
        - { match: "**/.env*", tools: [Write, Edit], verdict: block, why: not recoverable }

    — the broad rule matched first and `Write(.env)` came back CONFIRM. The stricter rule the
    operator had just declared never ran, and nothing said so. The only way to get the intended
    verdict was to order them narrowest-first, which is the opposite of how people read a list.

    Returning all of them puts this axis back under the same rule as every other one in `decide`:
    verdicts JOIN, so a declaration can only ever raise the outcome. A rule written too widely still
    costs a confirmation and never a silent allow — and now a rule written too *narrowly* cannot
    quietly cancel a stricter neighbour either.

    A rule with no `match` is skipped here: it fires on the tool alone and has already been
    considered, because it does not need a target to be true.
    """
    from neti.core.globs import matches

    return tuple(
        rule
        for rule in rules
        if rule.match and _applies_to(tool, rule) and matches(target, (rule.match,)) is not None
    )


def _sensitive_rule(tool: str, hits: tuple[tuple[str, Any], ...], worst: Verdict) -> str:
    """Name the sensitivity rule in the record, but only when it is what decided the call.

    A rule that named an operation rather than a target is credited as `sensitive:tool:<name>`,
    the same spelling `Taint` uses for the same idea — there is no glob to print, and printing an
    empty one would read as a rule that matched everything.
    """
    for pointer, rule in hits:
        if rule.verdict is worst:
            return f"{pointer}:sensitive:{rule.match}" if rule.match else f"sensitive:tool:{tool}"
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
