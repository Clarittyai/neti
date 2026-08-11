"""Declared cumulative budgets — the mitigation for SCOPE.md NC-01.

The hole this closes: a per-call gate cannot see 4,000 individual sends, because each call resolves
to a magnitude of 1 and passes every per-call ceiling. Per-call resolution is structurally blind to
it. So the operator declares a *cumulative* ceiling per unit per window, and the running total is
compared to it the same way a single magnitude is.

This stays deterministic and stays out of anomaly-detection territory because the number is
declared, not learned. Nothing about the observed distribution reaches this code; `neti propose`
prints suggestions for a human to edit into config, and that is the only channel.

**Windows.** A session is a conversation, and a conversation ends. That is the right frame for "this
agent went haywire in one run" and the wrong one for "this agent has been quietly reading
everything for three days" — the second is `glean-bulk-download` in the incident corpus, and a
per-session budget cannot see it because each new session starts at zero. So `window:` accepts four
forms, parsed here and keyed to storage by `neti.store.sessions`:

    session       one conversation. Resets when the agent starts a new one.
    day           a UTC calendar day.
    week          a UTC ISO week.
    rolling:24h   the last N hours, always. 1..168.

**Calendar windows reset on a boundary, and that is a real property rather than a subtlety.** A
`day` budget of 20,000 permits 40,000 across a single midnight. `rolling:` is the form with no
boundary to straddle, and is the one to declare when the number is a safety limit rather than an
accounting period. Both ship because both are things people mean; neither is inferred, and this
docstring is where the difference is written down rather than discovered.

This module reads no clock — `neti.core` cannot, and `tests/property/test_core_is_pure.py` enforces
it. A window is *parsed* here and *resolved to a bucket* by the caller, which is what keeps a
recorded decision reproducible.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import Field, field_serializer, field_validator, model_validator

from neti.core.decide import worst_tripped_band
from neti.core.types import ArgDecision, Band, BudgetDecision, Frozen, sorted_bands
from neti.core.units import Unit
from neti.core.verdict import ResolutionState, Verdict

__all__ = ["BudgetRule", "SessionTally", "Window", "WindowKind", "check_budgets"]

MAX_ROLLING_HOURS = 168
"""A week. The bound is not arbitrary: a rolling window is stored as one hourly sub-total per hour
in range, so this is what keeps the read a fixed small cost rather than an unbounded one."""


class WindowKind(StrEnum):
    SESSION = "session"
    DAY = "day"
    WEEK = "week"
    ROLLING = "rolling"


class Window(Frozen):
    """A parsed `window:` value. Immutable, clock-free, and round-trips to its declared spelling."""

    kind: WindowKind = WindowKind.SESSION
    hours: int = 0
    """Only meaningful for `ROLLING`. Zero everywhere else."""

    @classmethod
    def parse(cls, raw: str) -> Window:
        """Read a declared `window:`, or say exactly what is wrong with it.

        Unparseable is an error rather than a fallback to `session`. A window nobody validated is
        the dead-config failure this project keeps finding: `window: dayly` would have been accepted
        in silence and counted per conversation forever, and the operator would have had every
        reason to believe a daily budget was running.
        """
        text = (raw or "").strip()
        if not text:
            return cls()
        if text in (WindowKind.SESSION, WindowKind.DAY, WindowKind.WEEK):
            return cls(kind=WindowKind(text))
        prefix, _, amount = text.partition(":")
        if prefix == WindowKind.ROLLING and amount.endswith("h") and amount[:-1].isdigit():
            hours = int(amount[:-1])
            if not 1 <= hours <= MAX_ROLLING_HOURS:
                raise ValueError(
                    f"window {text!r} is out of range: rolling windows run from 1h to "
                    f"{MAX_ROLLING_HOURS}h (a week)."
                )
            return cls(kind=WindowKind.ROLLING, hours=hours)
        raise ValueError(
            f"cannot read {raw!r} as a window. Use `session`, `day`, `week`, "
            f"or `rolling:<n>h` (1h-{MAX_ROLLING_HOURS}h)."
        )

    def __str__(self) -> str:
        return f"rolling:{self.hours}h" if self.kind is WindowKind.ROLLING else self.kind.value


class BudgetRule(Frozen):
    """`unit` totals across `tools` within one `window` are compared against `bands`."""

    tools: frozenset[str]
    unit: Unit
    bands: tuple[Band, ...] = ()
    window: Window = Field(default_factory=Window)

    @field_validator("window", mode="before")
    @classmethod
    def _parse_window(cls, value: object) -> object:
        """`window: day` in YAML arrives as a string; `Window.parse` is the only way in."""
        return Window.parse(value) if isinstance(value, str) else value

    @field_serializer("window")
    def _spell_window(self, window: Window) -> str:
        """Serialised as the operator wrote it, because this goes into `Policy.digest()`.

        A dict here would make the digest depend on the *representation* of a window rather than on
        the window, so adding a field to `Window` would silently repolicy every agent in the fleet.
        """
        return str(window)

    @model_validator(mode="after")
    def _sort_and_check(self) -> BudgetRule:
        # Sorted for the same reason Ceiling sorts: so a human reading a record sees the tightest
        # applicable ceiling first. Correctness no longer depends on it — `worst_tripped_band`
        # selects by severity — but an unsorted list here was a real under-enforcement bug once, so
        # the invariant is asserted rather than assumed.
        object.__setattr__(self, "bands", sorted_bands(self.bands))
        return self

    @field_serializer("tools")
    def _sorted_tools(self, tools: frozenset[str]) -> list[str]:
        """Sorted, because this is dumped into `Policy.digest()`.

        A `frozenset` serialises in *hash order*, which varies with `PYTHONHASHSEED` — so the same
        policy file produced a different digest in different processes, and that digest is stamped
        into every decision record. Two agents on one config were recording two different policies,
        `neti verify` could not have noticed, and an approval bound to a policy digest could never
        be redeemed by the process that asked for it. Found by an approval that refused to match
        itself across a retry.

        The set is kept for `applies_to`'s O(1) lookup; only the serialised form is ordered.
        """
        return sorted(tools)

    def applies_to(self, tool: str) -> bool:
        return tool in self.tools


class SessionTally(Frozen):
    """Running totals per unit for one session.

    Immutable: `add` returns a new tally, so a decision can be computed against a candidate total
    without committing it. That matters because in `enforce` mode a blocked call must not consume
    budget — otherwise a single blocked attempt would poison the rest of the session.
    """

    totals: dict[str, int] = Field(default_factory=dict)
    calls: int = 0

    def total(self, unit: Unit) -> int:
        return self.totals.get(unit.value, 0)

    def add(self, unit: Unit, magnitude: int) -> SessionTally:
        merged = dict(self.totals)
        merged[unit.value] = merged.get(unit.value, 0) + magnitude
        return SessionTally(totals=merged, calls=self.calls + 1)

    def add_committed(self, args: tuple[ArgDecision, ...]) -> SessionTally:
        """Fold in the magnitudes of a call that actually executed."""
        tally = self
        for arg in args:
            res = arg.resolution
            if res.state is ResolutionState.RESOLVED and res.magnitude is not None:
                tally = tally.add(res.unit, res.magnitude)
        return SessionTally(totals=tally.totals, calls=self.calls + 1)


def _named(rule: BudgetRule, suffix: str) -> str:
    """How a fired budget names itself in the record.

    A `session` budget spells itself exactly as it always did, so records written before windows
    existed keep re-deriving to the same string. Anything else carries its window, because
    `objects_total>20000` means two different things once two windows can declare it.
    """
    window = "" if rule.window.kind is WindowKind.SESSION else f"@{rule.window}"
    return f"{rule.unit.value}_total>{suffix}{window}"


def check_budgets(
    tool: str,
    args: tuple[ArgDecision, ...],
    tallies: Mapping[str, SessionTally],
    rules: tuple[BudgetRule, ...],
) -> BudgetDecision:
    """Worst verdict across every applicable budget rule, given this call's contribution.

    `tallies` is keyed by the *spelling* of a window — the caller resolves each window to a storage
    bucket and loads its running total, because that resolution needs a clock and this module may
    not have one. A window with no entry counts as empty, which under-counts rather than
    over-blocks: the same direction `SessionStore` degrades in, and the same reasoning.

    An unresolved magnitude contributes nothing to the running total — deliberately. Inventing a
    contribution would make the total fiction; the per-argument `on_unresolved` verdict is what
    handles that case, and it has already fired by the time we get here.
    """
    applicable = [r for r in rules if r.applies_to(tool) and r.bands]
    if not applicable:
        return BudgetDecision()

    empty = SessionTally()
    worst: BudgetDecision | None = None
    # Sorted by (window, unit) so that two rules tripping at the same verdict resolve to the same
    # decision on every machine. Dict iteration order would have been stable here too; being
    # explicit is what makes it a property rather than an accident.
    for rule in sorted(applicable, key=lambda r: (str(r.window), r.unit.value)):
        contribution = sum(
            arg.resolution.magnitude or 0
            for arg in args
            if arg.resolution.unit is rule.unit and arg.resolution.state is ResolutionState.RESOLVED
        )
        candidate_total = tallies.get(str(rule.window), empty).total(rule.unit) + contribution
        band = worst_tripped_band(candidate_total, rule.bands)
        if band is None:
            continue
        decision = BudgetDecision(
            verdict=band.verdict,
            unit=rule.unit,
            running_total=candidate_total,
            tripped=band,
            rule=_named(rule, str(band.above)),
        )
        if worst is None or decision.verdict > worst.verdict:
            worst = decision

    if worst is None:
        units = sorted({r.unit.value for r in applicable})
        return BudgetDecision(
            verdict=Verdict.ALLOW,
            running_total=sum(tallies.get(str(r.window), empty).total(r.unit) for r in applicable),
            rule=f"under_budget:{'+'.join(units)}",
        )
    return worst
