"""The data model. Pure: these types hold values, they never fetch them.

`Resolution` is produced by a resolver (which does I/O) and consumed by `decide` (which does not).
That split is what makes a decision replayable: store the resolutions, replay the decision.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from neti.core.units import Direction, Unit
from neti.core.verdict import ResolutionState, Verdict, VerdictValue

Magnitude = Annotated[int, Field(ge=0)]


class Frozen(BaseModel):
    """Immutable, strict base. Extra fields are an error: a typo in a policy file must not be
    silently ignored, because a silently-ignored ceiling is an ungated tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- resolution


class Resolution(Frozen):
    """What a resolver learned about one target.

    `magnitude` is `None` unless `state is RESOLVED`, enforced below, so that a missing count can
    never be read as zero. That single validator is the difference between "we could not reach the
    directory" and "the group is empty".
    """

    state: ResolutionState
    unit: Unit
    magnitude: Magnitude | None = None
    direction: Direction = Direction.EXACT
    resolved_at: datetime | None = None
    consistency: Literal["strong", "eventual"] = "eventual"
    provider_snapshot: str | None = None
    breakdown: dict[str, Magnitude] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _magnitude_iff_resolved(self) -> Resolution:
        if self.state is ResolutionState.RESOLVED:
            if self.magnitude is None:
                raise ValueError("RESOLVED resolution must carry a magnitude")
        elif self.magnitude is not None:
            raise ValueError(
                f"{self.state.name} resolution must not carry a magnitude "
                "(a partial or failed count must never be readable as a number)"
            )
        return self

    @classmethod
    def resolved(
        cls,
        unit: Unit,
        magnitude: int,
        *,
        direction: Direction = Direction.EXACT,
        **kw: Any,
    ) -> Resolution:
        return cls(
            state=ResolutionState.RESOLVED,
            unit=unit,
            magnitude=magnitude,
            direction=direction,
            **kw,
        )

    @classmethod
    def unresolved(cls, unit: Unit, reason: str, **kw: Any) -> Resolution:
        ev: dict[str, Any] = {"reason": reason}
        ev.update(kw.pop("evidence", {}))
        return cls(state=ResolutionState.UNRESOLVED, unit=unit, evidence=ev, **kw)

    @classmethod
    def partial(cls, unit: Unit, reason: str, **kw: Any) -> Resolution:
        """Enumeration was truncated. Deliberately carries no magnitude — see the validator."""
        ev: dict[str, Any] = {"reason": reason}
        ev.update(kw.pop("evidence", {}))
        return cls(state=ResolutionState.PARTIAL, unit=unit, evidence=ev, **kw)


# --------------------------------------------------------------------------- policy


class Band(Frozen):
    """`above` is exclusive: a magnitude of exactly `above` does not trip the band."""

    above: Magnitude
    verdict: VerdictValue


def sorted_bands(bands: Iterable[Band]) -> tuple[Band, ...]:
    """Descending by threshold, with duplicates rejected.

    Every model that holds bands calls this. It exists as a shared function rather than three
    copies of a validator because it has already gone wrong once: `BudgetRule` did not sort, and a
    budget declared `confirm above 200, block above 1000` silently returned CONFIRM at 1001. The
    decision path no longer depends on the order — `worst_tripped_band` selects by severity — but
    display, `has_ceiling` and `block_at` all read the first entry, so the invariant has to hold
    everywhere or it holds nowhere.
    """
    ordered = tuple(sorted(bands, key=lambda b: b.above, reverse=True))
    if len({b.above for b in ordered}) != len(ordered):
        raise ValueError("duplicate band thresholds make the applicable band ambiguous")
    return ordered


class Breach(Frozen):
    """One ceiling a resolution exceeded.

    A decision records all of them, not just the deciding one — see `_all_breaches`. `source` is
    `"magnitude"` or `"breakdown:<key>"`.
    """

    source: str
    observed: Magnitude
    above: Magnitude
    verdict: VerdictValue


class Ceiling(Frozen):
    """The operator's declaration for one gated argument.

    `bands` are stored descending by `above`, so the first match is the most severe applicable.
    """

    unit: Unit
    bands: tuple[Band, ...] = ()
    on_unresolved: VerdictValue = Verdict.BLOCK
    """Verdict when the resolver could not learn the magnitude (UNRESOLVED or PARTIAL)."""

    on_unbounded: VerdictValue = Verdict.CONFIRM
    """Verdict when the magnitude is under every band but the direction cannot justify an allow."""

    breakdown_bands: dict[str, tuple[Band, ...]] = Field(default_factory=dict)
    """Bands applied to named sub-counts, e.g. `{"guest": (Band(above=100, ...),)}`."""

    @model_validator(mode="after")
    def _sort_and_check(self) -> Ceiling:
        object.__setattr__(self, "bands", sorted_bands(self.bands))
        object.__setattr__(
            self,
            "breakdown_bands",
            {k: sorted_bands(v) for k, v in self.breakdown_bands.items()},
        )
        return self


# --------------------------------------------------------------------------- calls


class ProposedCall(Frozen):
    """A tool call the agent wants to make, before it is made."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = None
    session_id: str | None = None


UNREADABLE = "__neti_unparseable__"
"""Key under which an argument payload we could not read is preserved.

Every runtime hands arguments over in its own shape — a dict, a JSON string, whatever the model
emitted — and each adapter has to reduce that to a dict before a policy can point at it. What to do
when it will not reduce is the interesting case, and the adapters had answered it differently: the
OpenAI one kept a truncated copy under this key, the Anthropic one substituted `{}`.

Both reach the *same verdict*, which is why this went unnoticed: an absent gated argument and an
unreadable one both resolve to `None` and take the declared `on_unresolved`. The difference is in
the record, which is the artefact an auditor reads — `{}` says a call arrived with no arguments,
and that is not what happened. A gate that cannot tell "nothing was sent" from "something arrived
that we could not read" has lost the more alarming of the two.
"""


def unreadable_arguments(raw: object) -> dict[str, Any]:
    """Reduce whatever a runtime handed over to arguments a policy can be pointed at.

    A malformed payload is never `{}`. Returning an empty dict would let a policy see no gated
    parameter and pass the call through — a parse failure quietly becoming a permissive verdict.
    The sentinel below is unresolvable by every resolver, so the declared `on_unresolved` decides,
    and the payload survives into the record.
    """
    import json

    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {UNREADABLE: str(raw)[:200]}
    return parsed if isinstance(parsed, dict) else {UNREADABLE: str(raw)[:200]}


# --------------------------------------------------------------------------- decisions


class ArgDecision(Frozen):
    """The verdict for one gated argument, keyed by its JSON pointer."""

    pointer: str
    target: str | None
    verdict: Verdict
    resolution: Resolution
    rule: str
    """Which branch of the procedure fired. Goes in the record so a verdict is explainable
    without re-running it."""

    over_block_possible: bool = False
    """Set when a block rests on an upper bound, so the true magnitude may be under the ceiling.
    Not an error — the friction metric (M5) needs to count these."""

    tripped: Band | None = None
    """The deciding band. Redundant with the worst entry in `breaches`, kept because it is what
    the denial message quotes back to the agent."""

    breaches: tuple[Breach, ...] = ()
    """Every ceiling exceeded, so the record does not hide a second breach behind the first."""


class BudgetDecision(Frozen):
    """The verdict from declared cumulative session budgets. See SCOPE.md NC-01."""

    verdict: Verdict = Verdict.ALLOW
    unit: Unit | None = None
    running_total: Magnitude = 0
    tripped: Band | None = None
    rule: str = "no_budget_declared"


class Decision(Frozen):
    verdict: Verdict
    args: tuple[ArgDecision, ...] = ()
    budget: BudgetDecision | None = None
    tool: str = ""
    mode_applied: Literal["observe", "enforce"] = "observe"
    rule: str = ""

    @property
    def proceeds(self) -> bool:
        """Whether the upstream call is made. In observe mode it always is."""
        return self.mode_applied == "observe" or self.verdict.proceeds
