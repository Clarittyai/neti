"""The incident corpus, and an honest account of which ones `neti` would have stopped.

**Publishing the misses is the point.** A magnitude gate that claims the PocketOS database deletion
without shipping a bytes resolver is the kind of overclaim that loses a security audience in one
question, and this corpus exists so that the answer is already written down before the question is
asked. Four of the five entries below are misses or partial catches.

Every entry was verified during research, and two of the three anecdotes the project started with
turned out to be wrong:

- The nine-second database deletion is **PocketOS / Railway / Cursor+Opus 4.6 (Apr 2026)**, not
  Replit. Railway also restored the data within the hour, so "every backup gone" is contested on the
  record and is not claimed here.
- The "4,000 records read, 4,000 emails sent" story is **unsourceable**. The 4,000 appears to have
  been borrowed from Replit's ~4,000 *fabricated* records, which is a different failure entirely. It
  has been replaced with incidents that have citable numbers.

`caught_by` names the resolver family an entry needs, which is what makes the coverage arithmetic
honest: an incident is only "covered" if a resolver for that family actually ships.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from neti.core.units import Unit

__all__ = ["INCIDENTS", "Coverage", "Incident", "replay"]


class Coverage(StrEnum):
    CAUGHT = "caught"
    """A shipped resolver sizes this, and a declared ceiling would have stopped it."""

    NEEDS_RESOLVER = "needs_resolver"
    """The mechanism applies, but the resolver for that unit is not built."""

    NEEDS_BUDGET = "needs_budget"
    """Per-call resolution is structurally blind; only a declared session budget sees it (NC-01)."""

    OUT_OF_SCOPE = "out_of_scope"
    """Magnitude is the wrong primitive. No resolver would help."""


@dataclass(frozen=True)
class Incident:
    id: str
    date: str
    actor: str
    what_one_call_did: str
    magnitude: int | None
    unit: Unit | None
    authorized: bool
    reversible: str
    source: str
    coverage: Coverage
    note: str
    """Why it lands where it lands. This is the sentence that gets read aloud."""


INCIDENTS: tuple[Incident, ...] = (
    Incident(
        id="remove-group-members",
        date="—",
        actor="the demo",
        what_one_call_did="remove_group_members on a nested all-engineering group",
        magnitude=41_203,
        unit=Unit.PRINCIPALS,
        authorized=True,
        reversible="no — group membership history is not retained",
        source="synthetic; the shape of the identity-blast-radius case",
        coverage=Coverage.CAUGHT,
        note=(
            "One O(1) Graph call sizes it before execution. This is the case the product is built "
            "for and the only one in this table it fully covers."
        ),
    ),
    Incident(
        id="claude-code-terraform",
        date="2026-02-26",
        actor="Claude Code",
        what_one_call_did="terraform destroy against production state",
        magnitude=1_943_200,
        unit=Unit.ROWS,
        authorized=True,
        reversible="partially — a surviving snapshot was found after 24h",
        source="https://alexeyondata.substack.com/p/how-i-dropped-our-production-database",
        coverage=Coverage.NEEDS_RESOLVER,
        note=(
            "A Terraform plan resolver (plan JSON -> count of destroy actions) would size this. "
            "It is the highest-value resolver not yet built. Note it is a coverage win rather "
            "than a novelty one: `conftest` with `max_auto_apply_changes` already does exactly "
            "this in the IaC world."
        ),
    ),
    Incident(
        id="pocketos-railway",
        date="2026-04-24",
        actor="Cursor + Claude Opus 4.6",
        what_one_call_did="Railway volume-delete via a root token found in an unrelated file",
        magnitude=None,
        unit=Unit.BYTES,
        authorized=True,
        reversible="disputed — Railway restored within the hour; the founder describes a "
        "three-month-old backup and a weekend of manual reconstruction",
        source="https://www.theregister.com/2026/04/27/cursoropus_agent_snuffs_out_pocketos/",
        coverage=Coverage.NEEDS_RESOLVER,
        note=(
            "MISS. Needs a bytes/objects resolver for storage volumes, which is not built. Do "
            "not claim this incident. Note also that the proximate cause was an unscoped "
            "credential, which is an authorization problem upstream of a magnitude gate."
        ),
    ),
    Incident(
        id="glean-bulk-download",
        date="2026-03",
        actor="a Glean agent, via Obsidian telemetry",
        what_one_call_did="bulk document retrieval across a customer environment",
        magnitude=8_000_000,
        unit=Unit.OBJECTS,
        authorized=True,
        reversible="n/a — reads",
        source="https://www.obsidiansecurity.com/blog/ai-agent-toxic-risk-combinations",
        coverage=Coverage.NEEDS_BUDGET,
        note=(
            "MISS per-call. The volume accumulated across many retrievals, so per-call resolution "
            "sees a small number every time; only a declared session budget on `objects` sees the "
            "pattern (NC-01/NC-12). Also worth saying out loud when citing it: this is an "
            "enterprise-search agent whose job is broad retrieval, and no harm was stated."
        ),
    ),
    Incident(
        id="openclaw-inbox",
        date="2026-02-22",
        actor="OpenClaw, in a Meta safety director's inbox",
        what_one_call_did="hundreds of deletes and sends inside one OAuth grant",
        magnitude=None,
        unit=Unit.OBJECTS,
        authorized=True,
        reversible="largely no",
        source="https://techcrunch.com/2026/02/23/"
        "a-meta-ai-security-researcher-said-an-openclaw-agent-ran-amok-on-her-inbox/",
        coverage=Coverage.NEEDS_BUDGET,
        note=(
            "MISS per-call, for the same reason as NC-01: iterative single-item operations. A "
            "session budget catches it. The agent also ignored an explicit stop instruction, which "
            "is a control problem no magnitude gate addresses."
        ),
    ),
    Incident(
        id="nhs-email-storm",
        date="2016-11-14",
        actor="NHS England (human, pre-AI)",
        what_one_call_did="one message to a distribution list of 840,000",
        magnitude=840_000,
        unit=Unit.RECIPIENTS,
        authorized=True,
        reversible="n/a",
        source="https://en.wikipedia.org/wiki/Email_storm",
        coverage=Coverage.CAUGHT,
        note=(
            "Sized by the recipients resolver. Included deliberately as the honest framing of the "
            "wedge: this failure mode long predates agents, and Google Workspace and Purview "
            "already ship recipient-count controls. It is the recognisable demo, not the "
            "defensible claim."
        ),
    ),
    Incident(
        id="single-row-delete",
        date="—",
        actor="any agent",
        what_one_call_did="delete the one row that mattered",
        magnitude=1,
        unit=Unit.ROWS,
        authorized=True,
        reversible="depends",
        source="SCOPE.md NC-02",
        coverage=Coverage.OUT_OF_SCOPE,
        note=(
            "Structurally invisible. A cardinality of 1 is under every ceiling, and consequence is "
            "not cardinality. Included so the table cannot be read as a coverage claim."
        ),
    ),
)


def replay(shipped_units: frozenset[Unit]) -> dict[str, list[Incident]]:
    """Group the corpus by how `neti` fares, given the resolver units that actually ship.

    Coverage is a function of what is built, not of what is imagined, which is why this takes the
    shipped units as an argument rather than assuming them.
    """
    out: dict[str, list[Incident]] = {c.value: [] for c in Coverage}
    for incident in INCIDENTS:
        coverage = incident.coverage
        # An entry marked CAUGHT is only caught if its unit is actually resolvable today.
        if coverage is Coverage.CAUGHT and (
            incident.unit is None or incident.unit not in shipped_units
        ):
            coverage = Coverage.NEEDS_RESOLVER
        out[coverage.value].append(incident)
    return out
