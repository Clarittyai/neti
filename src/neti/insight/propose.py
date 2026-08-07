"""`neti propose` — turn "name a ceiling" into "review a number".

This exists because of a specific, measured problem: 1,032 comments on the canonical
agent-destroys-production incident produced **zero** requests for a numeric ceiling. Asking an
operator to invent one from a blank page is asking for a number they cannot defend, and a ceiling
nobody chose is a ceiling nobody will stand behind when it fires. So we do not ask. We show them
their own distribution and propose an edit.

**The determinism seam, and it must be stated every time this feature is shown.** What comes out of
here is *text a human edits into a config file*. It is never read at decision time, never fed back
into the gate, and never updated automatically. The gate stays a static integer comparison against
a number a person committed. That is the whole difference between this and the baseline-learning
anomaly detection the product deliberately is not, and it is the first seam a security reviewer will
probe.

**It refuses to guess.** Below `MIN_SAMPLES` observations there is no proposal, only a note saying
how much more traffic is needed. A confidently-wrong ceiling derived from nine calls is worse than
no ceiling, because it looks configured.

**Except for deletions, where that refusal was itself the bug.** A destructive gate never reaches
thirty observations, because deletions are *rare* — measured on a real project, 3 sizeable ones in
274 calls. So `propose` said "keep observing" forever, the operator never declared a `Bash` ceiling,
and `rm -rf node_modules` was sized at 968 objects and **allowed, in enforce mode**. A user who
followed every instruction correctly ended up protected against wide reads and not against deletion.

Waiting for a sample size that will not arrive is not caution, it is a silent absence. So a gate
whose resolver recognised its targets as destructive gets a proposal from whatever it has, labelled
as the judgement it is: for a deletion the question was never *what is normal here* but *what would
you not want to lose*, and traffic can only ever inform that, never answer it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from neti.insight.report import Distribution, ReportSummary

__all__ = ["MIN_SAMPLES", "Proposal", "format_proposals", "propose"]

MIN_DESTRUCTIVE_SAMPLES = 1
"""One sized deletion is enough to propose from, because a second may be months away.

Not a claim that one observation is statistically meaningful — it is not. It is a claim that a
number an operator reviews and edits beats the nothing they had, when the alternative is a gate
that is never configured at all."""

MIN_SAMPLES = 30
"""Below this, propose nothing. Not a statistical threshold so much as an honesty one: a ceiling
fitted to a handful of calls encodes the accident of that week."""

CONFIRM_MULTIPLE = 2
"""Confirm at twice normal: outside ordinary variation, but not so tight that it nags."""

BLOCK_MULTIPLE = 10
"""Block an order of magnitude beyond normal — the "this is not what you meant" line, not the
"this is unusual" line."""


@dataclass
class Proposal:
    tool: str
    pointer: str
    unit: str
    n: int
    normal: int
    """The value the ceilings are multiples of. See `_propose_one` and `anchor`."""

    observed_max: int
    confirm_above: int | None
    block_above: int | None
    rationale: str

    anchor: str = "p95"
    """Which percentile `normal` came from. Displayed, because printing `p95=25` when the real p95
    is 41,203 is a lie in the one output an operator is meant to check the arithmetic of."""

    would_confirm: int = 0
    would_block: int = 0

    would_escalate: int = 0
    """Calls under every proposed band that still cannot be allowed, because their resolution is a
    bound rather than a count. These take the gate's declared `on_unbounded` verdict no matter what
    numbers the operator picks — so they are interrupts the ceilings cannot tune away, and hiding
    them made the proposal look cheaper than it is."""

    unresolved: int = 0
    """Calls that could not be sized at all. They take `on_unresolved` and are likewise unaffected
    by the numbers below."""
    examples: list[int] = field(default_factory=list)
    """The largest observed calls this proposal would have stopped. The operator reviews these,
    not the arithmetic."""

    @property
    def actionable(self) -> bool:
        return self.confirm_above is not None


SPREAD = 10
"""How far the maximum must sit above the median before a distribution counts as having outliers.

Below this the traffic is one population and a ceiling above the maximum is the right answer — it
binds only on the unseen. Above it there are two populations, and a proposal that catches neither
is not a conservative proposal, it is a broken one."""


def _anchor(dist: Distribution) -> tuple[str, int, int, int]:
    """Pick the percentile to multiply, and reject one the traffic has already contaminated.

    Returns `(name, normal, confirm, block)`. p95 first, because it is right whenever the tail is
    genuinely a tail. The median as a fallback, because it survives any minority of outliers, at the
    cost of an interrupt rate the operator is shown before they commit anything.
    """
    fallback = ("p50", 1, 1, 1)
    for name, value in (("p95", dist.p95), ("p50", dist.p50)):
        normal = max(value, 1)
        confirm = _round_up_human(max(normal * CONFIRM_MULTIPLE, normal + 1))
        block = _round_up_human(max(normal * BLOCK_MULTIPLE, confirm * 2))
        fallback = (name, normal, confirm, block)

        bimodal = dist.maximum > max(dist.p50, 1) * SPREAD
        catches_something = any(m > confirm for m in dist.magnitudes)
        if not bimodal or catches_something:
            return name, normal, confirm, block
    return fallback


def _round_up_human(value: int) -> int:
    """Round to a number a person would actually write: 1, 2 or 5 times a power of ten.

    An operator reviewing `block above 1000` will engage with it. `block above 1147` reads as
    machine output and gets accepted without thought, which defeats the purpose of asking a human.
    """
    if value <= 10:
        return max(1, value)
    magnitude: int = 10 ** (len(str(value)) - 1)
    for multiple in (1, 2, 5, 10):
        candidate: int = multiple * magnitude
        if candidate >= value:
            return candidate
    return 10 * magnitude


def propose(summary: ReportSummary) -> list[Proposal]:
    return [_propose_one(d) for d in summary.ordered]


def _with_impact(proposal: Proposal, dist: Distribution) -> Proposal:
    """Fill in what these numbers would have done to the traffic already recorded.

    The same arithmetic the main path does inline. Shared rather than duplicated, because an IMPACT
    line that disagreed with the one two proposals above it would be worse than none.
    """
    confirm = proposal.confirm_above or 0
    block = proposal.block_above or 0
    return replace(
        proposal,
        would_block=sum(1 for m in dist.magnitudes if m > block),
        would_confirm=sum(1 for m in dist.magnitudes if confirm < m <= block),
        would_escalate=sum(
            1 for o in dist.observations if o.magnitude <= confirm and not o.can_clear_a_ceiling
        ),
        unresolved=dist.unresolved,
        examples=sorted((m for m in dist.magnitudes if m > confirm), reverse=True)[:3],
    )


def _destructive_proposal(dist: Distribution) -> Proposal:
    """A ceiling for a gate that deletes, from too few observations to be statistical.

    Anchored on the median deletion rather than the largest. The largest is usually the one you
    would want stopped — on the measured project the three sizeable deletions were 968, 968 and
    5,009, and a ceiling above the maximum would have caught none of them, which is the dead-config
    failure `_anchor` exists to avoid, in its most expensive form.

    So: confirm at roughly the routine deletion, block at a multiple of it. Both are stated as
    starting points, and the impact on the deletions already recorded is printed underneath.
    """
    routine = dist.quantile(0.5)
    confirm = _round_up_human(max(routine, 1))
    block = _round_up_human(max(routine * 5, confirm + 1))
    return Proposal(
        tool=dist.tool,
        pointer=dist.pointer,
        unit=dist.unit,
        n=dist.n,
        normal=routine,
        anchor="p50 of the deletions seen",
        observed_max=dist.maximum,
        confirm_above=confirm,
        block_above=block,
        rationale=(
            f"a destructive gate, and only {dist.n} sized deletion(s) — which is not a small "
            f"sample so much as the normal one, because deletions are rare. Waiting for "
            f"{MIN_SAMPLES} would leave this gate unconfigured indefinitely, so this proposes "
            f"from what there is. Anchored on the median deletion ({routine:,}) rather than the "
            f"largest ({dist.maximum:,}), because the largest is usually the one you would want "
            f"stopped and a ceiling above it would catch nothing. **Treat these as a starting "
            f"point you edit**: for a deletion the question is not what is normal here but what "
            f"you would not want to lose, and traffic can inform that, never answer it"
        ),
    )


def _propose_one(dist: Distribution) -> Proposal:
    # Deletions first, because the sample-size rule below is the wrong rule for them and its
    # refusal shipped as a silent absence.
    if dist.destructive and MIN_DESTRUCTIVE_SAMPLES <= dist.n < MIN_SAMPLES:
        return _with_impact(_destructive_proposal(dist), dist)

    if dist.n < MIN_SAMPLES:
        return Proposal(
            tool=dist.tool,
            pointer=dist.pointer,
            unit=dist.unit,
            n=dist.n,
            normal=dist.p95,
            observed_max=dist.maximum,
            confirm_above=None,
            block_above=None,
            rationale=(
                f"only {dist.n} observation(s); {MIN_SAMPLES} needed before a ceiling means "
                "anything. Keep observing."
            ),
        )

    # p95, not p99. The outliers are the *signal*, and at realistic sample sizes they land inside
    # the top percentile and redefine "normal" as themselves. A week of 143 calls — 140 of them
    # under 10 recipients, three of them huge — puts p99 at 4,100, which proposes a block ceiling
    # of 50,000 and would not have stopped the 41,203-recipient send it exists to stop. Anchoring
    # on p95 keeps the tail outside the definition of normal, which is the only way a proposal
    # derived from traffic can ever catch that traffic's own outliers.
    #
    # But p95 only holds while the outliers are rarer than 5% of traffic. Past that they *are* the
    # p95, and multiplying it produces a ceiling above everything ever observed — dead config that
    # reads as configured. Measured: 40 sends, 32 of 25 recipients and 4 of 41,203, proposed
    # `block above 500,000`. Twelve times the worst call ever made, and it could never fire.
    #
    # So the anchor is checked against the data rather than trusted — but "block is above the
    # observed max" is *not* the test, and getting that wrong the first time is instructive. With a
    # hundred identical 3-recipient sends, a ceiling above the max is exactly right: it binds only
    # on behaviour you have not seen yet. The failure is narrower than that. It is a distribution
    # that plainly *has* outliers — a maximum orders of magnitude above the median — paired with a
    # proposal that would have caught none of them.
    anchor, normal, confirm, block = _anchor(dist)

    # The three branches of `core/decide.py`, in its order, so this cannot drift from it:
    #   1. over a band          -> that band's verdict
    #   2. under every band but the direction cannot soundly allow -> `on_unbounded`
    #   3. under every band     -> allow
    #
    # Only branch 1 used to be counted, which made the IMPACT line quietly false for every resolver
    # that reports a bound rather than a count. `db.rows` measuring 3 rows does not clear a ceiling
    # of 100: cascades are invisible to it, so the truth is *at least* 3 and the call escalates.
    # Four of the nine shipped resolvers are in that position, and an operator reading "would have
    # asked about 4" was being told the interrupt rate was smaller than it is.
    would_block = sum(1 for m in dist.magnitudes if m > block)
    would_confirm = sum(1 for m in dist.magnitudes if confirm < m <= block)
    would_escalate = sum(
        1 for o in dist.observations if o.magnitude <= confirm and not o.can_clear_a_ceiling
    )
    examples = sorted((m for m in dist.magnitudes if m > confirm), reverse=True)[:3]

    rationale = f"{CONFIRM_MULTIPLE}x and {BLOCK_MULTIPLE}x the observed {anchor} ({normal:,})"
    if anchor == "p95":
        rationale += (
            f". p95 rather than p99 because at n={dist.n:,} the outliers sit inside the top "
            "percentile and would otherwise define normal as themselves"
        )
    else:
        rationale += (
            f". p95 was {dist.p95:,} here — your large calls are more than 5% of the traffic, so "
            "they *are* the p95, and multiplying it proposes a ceiling above everything you have "
            "ever done. Anchored on the median instead; expect a higher interrupt rate"
        )
    if dist.maximum > block * 5:
        rationale += (
            f". The observed maximum ({dist.maximum:,}) is far beyond this ceiling — check that "
            "it was intended"
        )

    return Proposal(
        tool=dist.tool,
        pointer=dist.pointer,
        unit=dist.unit,
        n=dist.n,
        normal=normal,
        anchor=anchor,
        observed_max=dist.maximum,
        confirm_above=confirm,
        block_above=block,
        rationale=rationale,
        would_confirm=would_confirm,
        would_block=would_block,
        would_escalate=would_escalate,
        unresolved=dist.unresolved,
        examples=examples,
    )


def format_proposals(proposals: list[Proposal]) -> str:
    if not proposals:
        return "Nothing observed yet. Run observe mode for a week, then try again."

    out: list[str] = [
        "Proposed ceilings, derived from YOUR observed traffic.",
        "",
        "These are a starting point for review, not a configuration. Edit the numbers, then paste",
        "them into your policy file. Nothing here is applied automatically and nothing computed",
        "here is ever read at decision time — the gate only ever compares against numbers you",
        "committed.",
        "",
    ]

    actionable = [p for p in proposals if p.actionable]
    waiting = [p for p in proposals if not p.actionable]

    for p in actionable:
        out.append(f"{p.tool} {p.pointer}:")
        out.append(
            f"  observed  n={p.n:,}  {p.anchor}={p.normal:,}  max={p.observed_max:,}  [{p.unit}]"
        )
        out.append(f"  proposed  confirm above {p.confirm_above:,}   block above {p.block_above:,}")
        out.append(f"  rationale {p.rationale}")
        # The line that actually gets reviewed. Arithmetic is not reviewable; consequences are.
        if p.would_block or p.would_confirm:
            out.append(
                f"  IMPACT    over the observed window this would have blocked "
                f"{p.would_block:,} call(s) and asked about {p.would_confirm:,}"
            )
            if p.examples:
                shown = ", ".join(f"{e:,}" for e in p.examples)
                out.append(f"            largest affected: {shown} {p.unit}")
        else:
            out.append(
                "  IMPACT    nothing in the observed window would have been stopped — "
                "these ceilings only bind on behaviour you have not seen yet"
            )

        # Reported *after* the ceilings and separately from them, because no choice of ceiling
        # changes it. An operator who tunes the numbers down to reduce interrupts and still sees
        # this rate should be able to tell which half they were arguing with.
        if p.would_escalate or p.unresolved:
            parts = []
            if p.would_escalate:
                parts.append(
                    f"{p.would_escalate:,} call(s) under these ceilings resolved to a bound "
                    "rather than a count (cascades, caps, members you cannot see)"
                )
            if p.unresolved:
                parts.append(f"{p.unresolved:,} could not be sized at all")
            out.append(f"  ALSO      {'; '.join(parts)}.")
            out.append(
                "            These take your declared on_unbounded / on_unresolved verdict "
                "whatever ceiling you pick."
            )
        out.append("")

    if actionable:
        # A merge fragment, not a standalone policy. Traffic can only have been observed for a
        # parameter that was already gated, so the `resolver:` line already exists upstream and
        # emitting our own would invite someone to overwrite a working one.
        out.append("# merge these bands into the gates you already have, keeping each")
        out.append("# existing `resolver:` line, once you are satisfied with the numbers:")
        out.append("")
        by_tool: dict[str, list[Proposal]] = {}
        for p in actionable:
            by_tool.setdefault(p.tool, []).append(p)
        out.append("tools:")
        for tool, group in by_tool.items():
            out.append(f"  {tool}:")
            out.append("    gate:")
            for p in group:
                out.append(f"      {p.pointer}:")
                out.append("        bands:")
                out.append(f"          - {{ above: {p.confirm_above}, verdict: confirm }}")
                out.append(f"          - {{ above: {p.block_above}, verdict: block }}")
        out.append("")

    if waiting:
        out.append("Not enough traffic to propose anything for:")
        for p in waiting:
            out.append(f"  {p.tool} {p.pointer} — {p.rationale}")

    return "\n".join(out)
