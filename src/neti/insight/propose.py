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
"""

from __future__ import annotations

from dataclasses import dataclass, field

from neti.insight.report import Distribution, ReportSummary

__all__ = ["MIN_SAMPLES", "Proposal", "format_proposals", "propose"]

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
    """The robust centre of the observed distribution — p95, not p99. See `_propose_one`."""

    observed_max: int
    confirm_above: int | None
    block_above: int | None
    rationale: str
    would_confirm: int = 0
    would_block: int = 0
    examples: list[int] = field(default_factory=list)
    """The largest observed calls this proposal would have stopped. The operator reviews these,
    not the arithmetic."""

    @property
    def actionable(self) -> bool:
        return self.confirm_above is not None


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


def _propose_one(dist: Distribution) -> Proposal:
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
    normal = max(dist.p95, 1)
    confirm = _round_up_human(max(normal * CONFIRM_MULTIPLE, normal + 1))
    block = _round_up_human(max(normal * BLOCK_MULTIPLE, confirm * 2))

    would_confirm = sum(1 for m in dist.magnitudes if confirm < m <= block)
    would_block = sum(1 for m in dist.magnitudes if m > block)
    examples = sorted((m for m in dist.magnitudes if m > confirm), reverse=True)[:3]

    rationale = (
        f"{CONFIRM_MULTIPLE}x and {BLOCK_MULTIPLE}x the observed p95 ({normal:,}). "
        f"p95 rather than p99 because at n={dist.n:,} the outliers sit inside the top percentile "
        "and would otherwise define normal as themselves"
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
        observed_max=dist.maximum,
        confirm_above=confirm,
        block_above=block,
        rationale=rationale,
        would_confirm=would_confirm,
        would_block=would_block,
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
        out.append(f"  observed  n={p.n:,}  p95={p.normal:,}  max={p.observed_max:,}  [{p.unit}]")
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
