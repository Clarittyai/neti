"""`neti score` — the artifact that goes in a deck and, eventually, in a paper.

Two rules shape it.

**The blind spots are part of the output, not an appendix.** A scorecard that reports only what the
product catches is marketing. The incident table below is mostly misses, and it prints them first
among equals rather than in a footnote, because a security audience that finds the gap themselves
stops believing the rest of the numbers.

**Measurements that need a live tenant are listed as absent, not estimated.** M2 (latency) and M6
(time to first value) cannot be produced offline. Printing a modelled figure next to measured ones
would make the whole card unciteable, so they appear as explicitly outstanding.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from neti.config.policy import Policy
from neti.core.units import Unit
from neti.eval.incidents import Coverage, Incident, replay
from neti.insight.report import ReportSummary

__all__ = [
    "EVIDENCE",
    "LIVE_VERIFIED",
    "RESOLVERS",
    "SEAMS",
    "Scorecard",
    "Wild",
    "build_scorecard",
    "format_scorecard",
    "scorecard_json",
]

# Units a shipped resolver can size today. Everything else is an honest gap.
SHIPPED_UNITS = frozenset({Unit.PRINCIPALS, Unit.APPS, Unit.RECIPIENTS, Unit.RESOURCES})

RESOLVERS = frozenset(
    {
        "entra.principals",
        "entra.apps",
        "entra.guests",
        "entra.principals_with_guests",
        "fs.paths",
        "db.rows",
        "storage.objects",
        "github.repos",
        "github.files",
        "terraform.destroy",
    }
)
"""Every resolver `resolvers_for_client` registers.

A second copy of a list that already exists, which is normally how NC-10 went stale — so it is
pinned by `tests/property/test_scorecard_is_true.py` rather than by good intentions. It is copied
here deliberately: building the real registry needs `httpx`, and `neti score` must not require the
`graph` extra to print a coverage number about resolvers that need no credential at all.
"""

SEAMS: dict[str, str] = {
    "hook": "Claude Code `PreToolUse` — the harness's own built-ins, which no proxy can see",
    "mcp-stdio": "`neti gate --stdio` in front of a local MCP server",
    "mcp-http": "`neti gate --upstream` in front of a remote one",
    "preflight": "`Preflight.check` / `.dispatch` / `@guard`, called directly",
    "tool-loop": "a hand-written Anthropic Messages or OpenAI Chat Completions loop",
    "anthropic": "the Anthropic `tool_runner`",
    "openai-agents": "the OpenAI Agents SDK, via `tool_input_guardrails`",
    "langchain": "LangChain and LangGraph",
    "crewai": "CrewAI, via a `before_tool_call` / `after_tool_call` pair",
    "pydantic-ai": "Pydantic AI, via `before_tool_execute`",
    "autogen": "AutoGen, by wrapping the workbench",
    "google-adk": "Google ADK, via a plugin `before_tool_callback`",
}
"""M8 — every door a call can arrive through, and therefore every place a verdict could diverge.

The claim is not that eleven adapters exist. It is that they *agree*: one table in
`tests/e2e/test_seam_equivalence.py` drives all of them across all five resolver families and
asserts the same verdict, the same magnitude and the same denial sentence byte for byte. A verdict
that depends on which door a call came through is a bug in the product, not in the adapter.

Listed here rather than counted, because the number on its own would say nothing about which
runtimes an operator can actually install in front of. Pinned by
`tests/property/test_scorecard_is_true.py` against `neti.adapters`, so a shipped adapter missing
from this list fails the build — which is the same mechanism `RESOLVERS` below uses, and for the
same reason: a second copy of a list is how a coverage claim goes stale.
"""

_RESOLVER_FAMILIES = ("entra", "fs", "db", "storage", "terraform")
"""The worlds `tests/e2e/worlds.py` drives every seam against.

Named rather than counted for the same reason as `SEAMS`, and pinned by the same property test.
Until these existed, the seam table proved agreement about Entra and nothing else — four of the ten
resolvers had never crossed a seam boundary at all.
"""

LIVE_VERIFIED: dict[str, str] = {
    "github.repos": "api.github.com",
    "github.files": "api.github.com",
    "db.rows": "Postgres 16, in Docker",
    "storage.objects": "the S3 API, via MinIO in Docker",
    "terraform.destroy": "plans from a real terraform, via the null provider",
    # No provider to be live against — it walks a directory. Listed as verified rather than as a
    # gap, because the real thing it could get wrong is scale, and it has met scale: a 712,359-file
    # tree, which is where the capped `≥` floor came from.
    "fs.paths": "real filesystems, including a 712,359-file tree",
}
"""M11 — which resolvers have been run against a real provider, and which one.

Not a quality ranking: an unverified resolver is not a broken one, it is one whose *shape* has only
ever been asserted against a fixture we wrote. Every defect the live tier has found was invisible to
the offline suite, including a wrong `EXACT` from GitHub and a connection `db.rows` never closed, so
the distinction is worth printing. The Entra half stays absent until somebody has a tenant.

Each entry names a module under `tests/live/`; the same property test checks they exist.
"""

EVIDENCE: dict[str, str] = {
    "M4": "src/neti/eval/incidents.py",
    "M5": "the record chain named by --records",
    "M8": "tests/e2e/test_seam_equivalence.py",
    "M10": "eval/surveys/mcp_coverage.py + tests/corpus/",
    "M11": "tests/live/",
    "POLICY": "the policy named by --config",
    "BLIND SPOTS": "SCOPE.md",
    # The one section whose honest answer is "none", and it is not a gap in the rule — it is the
    # rule working. This is the list of things nothing has produced yet, so citing evidence for it
    # would be the exact inversion the card exists to prevent.
    "NOT YET MEASURED": "nothing yet — that is what this section is",
}
"""Which artefact backs each section of this card.

The rule the project already applies to itself, made mechanical: *a trial that does not end as a
number on `neti score` does not count*, and its converse — a number on `neti score` that nothing
produced does not count either. Every section prints the thing a reader can go and check, and
`tests/property/test_scorecard_is_true.py` fails when a cited path does not exist.

It is cheap and it is the difference between a card and a claim. A section added here without
evidence fails the build; a section added to the card without an entry here fails it too. That is
the only mechanism that survives somebody adding a metric in a hurry six months from now.
"""

NON_COVERAGE = {
    "NC-01": "cumulative effect across calls (only declared session budgets see it)",
    "NC-02": "correctness of the action — deleting the one row that mattered",
    "NC-03": "which tool was called, in what order, or what was omitted",
    "NC-04": "whether the caller should be doing this at all (authorization is upstream)",
    "NC-05": "low-cardinality, high-consequence targets",
    "NC-06": "Exchange dynamic distribution groups (invisible to Graph)",
    "NC-07": "entitlements inside downstream apps — one hop only",
    "NC-08": "the eventual-consistency window; we sell an auditable bound, not freshness",
    "NC-09": "ungated tools and undeclared parameters",
    "NC-10": (
        "exact row counts, and statements db.rows does not certainly recognise "
        "(cascades are invisible, so every count is a lower bound)"
    ),
    "NC-11": "containment and rollback",
    "NC-12": "reads that are individually small but collectively large",
    "NC-13": (
        "a record chain with a gap: an unwritable records path loses the evidence, not the "
        "verdict — the call is still gated, and `neti verify` reports the break"
    ),
}


@dataclass
class Friction:
    """M5. What a policy would cost the people using it."""

    calls: int = 0
    stopped: int = 0
    confirmed: int = 0
    blocked: int = 0
    over_block_possible: int = 0

    @property
    def interrupt_rate(self) -> float:
        return 0.0 if not self.calls else self.stopped / self.calls


@dataclass
class Wild:
    """M10. What `neti init` gates on a machine that looks like somebody's, not like a fixture.

    Produced by `eval/surveys/mcp_coverage.py` — it launches real MCP servers and asks each one
    `tools/list`. Absent unless that survey has been run, which is the point: this number cannot be
    derived, only measured.
    """

    servers_launched: int = 0
    servers_in_catalogue: int = 0
    tools_discovered: int = 0
    tools_gated: int = 0
    tools_sizable_in_principle: int = 0

    @property
    def gated_rate(self) -> float:
        return 0.0 if not self.tools_discovered else self.tools_gated / self.tools_discovered


@dataclass
class Scorecard:
    incidents: dict[str, list[Incident]] = field(default_factory=dict)
    friction: Friction = field(default_factory=Friction)
    wild: Wild | None = None
    policy_digest: str | None = None
    gated_tools: int = 0
    gated_params: int = 0
    params_without_ceiling: int = 0
    unresolved: int = 0
    outstanding: list[str] = field(default_factory=list)

    @property
    def covered(self) -> int:
        return len(self.incidents.get(Coverage.CAUGHT.value, []))

    @property
    def total_incidents(self) -> int:
        return sum(len(v) for v in self.incidents.values())


def build_scorecard(
    summary: ReportSummary | None = None,
    policy: Policy | None = None,
    *,
    shipped_units: frozenset[Unit] = SHIPPED_UNITS,
    wild: Wild | None = None,
) -> Scorecard:
    card = Scorecard(incidents=replay(shipped_units), wild=wild)

    card.outstanding = [
        "M1 resolution correctness — covered by the offline suite against the synthetic tenant",
        "M2 latency — REQUIRES A LIVE TENANT. Run `neti measure`. Every figure in the plan is "
        "modelled and no published Graph p50/p99 exists.",
        "M3 failure-mode matrix — covered by the offline suite",
        "M6 time to first value — the Entra half REQUIRES A TENANT. The coding-agent half needs "
        "only a clean machine: time `neti install` to the first `neti inventory` finding.",
        "Guest breakdown (risk R2) — UNVERIFIED. That `$filter=userType eq 'Guest'` works on the "
        "cast transitiveMembers collection with `$count` has not been confirmed against Graph.",
        "M7 denial response — UNMEASURED. What a real model does after a denial (narrows, stops, "
        "loops, or routes around the gate through an ungated tool) has never been observed. No "
        "LLM has been in the loop in this repository.",
    ]
    if card.wild is None:
        card.outstanding.append(
            "M10 coverage in the wild — NOT RUN here. `uv run python -m eval.surveys.mcp_coverage` "
            "launches real MCP servers and counts what `neti init` gates."
        )

    if policy is not None:
        card.policy_digest = policy.digest()
        for tool in policy.tools:
            specs = policy.gate_specs(tool)
            if specs:
                card.gated_tools += 1
            for spec in specs.values():
                card.gated_params += 1
                if not spec.has_ceiling:
                    card.params_without_ceiling += 1

    if summary is not None:
        friction = Friction(calls=summary.decisions)
        friction.blocked = summary.verdicts.get("block", 0)
        friction.confirmed = summary.verdicts.get("confirm", 0)
        friction.stopped = friction.blocked + friction.confirmed
        for dist in summary.distributions.values():
            card.unresolved += dist.unresolved
        card.friction = friction

    return card


def format_scorecard(card: Scorecard) -> str:
    out: list[str] = ["neti scorecard", "=" * 72, ""]

    out.append("M4  INCIDENT REPLAY")
    out.append(f"    evidence: {EVIDENCE['M4']}")
    out.append(
        f"    {card.covered} of {card.total_incidents} corpus entries are sized by a shipped "
        "resolver. The rest are listed because they are the questions you will be asked."
    )
    out.append("")
    labels = {
        Coverage.CAUGHT.value: "caught",
        Coverage.NEEDS_RESOLVER.value: "MISS — resolver not built",
        Coverage.NEEDS_BUDGET.value: "MISS per-call — needs a declared session budget",
        Coverage.OUT_OF_SCOPE.value: "out of scope — magnitude is the wrong primitive",
    }
    for key, label in labels.items():
        entries = card.incidents.get(key, [])
        if not entries:
            continue
        out.append(f"  [{label}]")
        for incident in entries:
            magnitude = f"{incident.magnitude:,}" if incident.magnitude is not None else "—"
            unit = incident.unit.value if incident.unit else ""
            # Where the gate sizes a different quantity than the one that was lost, say so on the
            # row rather than only in the note.
            if incident.gated_unit and incident.gated_unit is not incident.unit:
                unit = f"{unit} (gated: {incident.gated_unit.value})"
            out.append(f"    {incident.id:<24} {magnitude:>11} {unit:<11} {incident.date}")
            out.append(f"      {incident.note}")
        out.append("")

    out.append("M5  FRICTION (what the policy costs the people using it)")
    out.append(f"    evidence: {EVIDENCE['M5']}")
    f = card.friction
    if not f.calls:
        out.append("    no recorded traffic — run observe mode, then re-run with --records")
    else:
        out.append(
            f"    {f.calls:,} calls   blocked={f.blocked:,}   confirmed={f.confirmed:,}   "
            f"interrupt rate={f.interrupt_rate:.2%}"
        )
        if card.unresolved:
            out.append(
                f"    {card.unresolved:,} parameter(s) could not be resolved — each one is a call "
                "the gate could not size, and its declared on_unresolved verdict applied"
            )
    out.append("")

    out.append("M10 COVERAGE IN THE WILD (what `neti init` gates on a real machine)")
    out.append(f"    evidence: {EVIDENCE['M10']}")
    if card.wild is None:
        out.append("    not measured here — run `python -m eval.surveys.mcp_coverage`")
    else:
        w = card.wild
        out.append(
            f"    {w.servers_launched} of {w.servers_in_catalogue} catalogued MCP servers "
            f"launched   {w.tools_gated:,} of {w.tools_discovered:,} discovered tools gated "
            f"({w.gated_rate:.1%})"
        )
        out.append(
            f"    {w.tools_sizable_in_principle:,} further tool(s) carry a parameter some shipped "
            "resolver's name-rule matched and `neti init` then declined on context —"
        )
        out.append(
            "    an `owner` on a call that touches one issue, a `query` that is a web search. Each "
            "is written into the generated policy with its reason, to be overruled or left alone."
        )
        out.append(
            "    An ungated tool is out of scope, not denied (NC-09). This number is printed "
            "because a coverage claim nobody can check is marketing."
        )
    out.append("")

    out.append("M8 HARNESS COMPATIBILITY (every door a call can arrive through)")
    out.append(f"    evidence: {EVIDENCE['M8']}")
    for name, what in SEAMS.items():
        out.append(f"    {name:<16} {what}")
    out.append(
        f"    All {len(SEAMS)} are driven across all {len(_RESOLVER_FAMILIES)} resolver families "
        "by one table, and must agree on the verdict, the magnitude and"
    )
    out.append(
        "    denial sentence byte for byte. A verdict that depends on which door a call came "
        "through is a bug in the product."
    )
    out.append("")

    out.append("M11 LIVE PROVIDER VERIFICATION (resolvers run against something real)")
    out.append(f"    evidence: {EVIDENCE['M11']}")
    for name in sorted(RESOLVERS):
        against = LIVE_VERIFIED.get(name)
        mark = "verified" if against else "  —     "
        detail = f"against {against}" if against else "never run against a real provider"
        out.append(f"    [{mark}] {name:<30} {detail}")
    out.append(
        "    An unverified resolver is not a broken one. It is one whose shape has only ever been "
        "checked against a fixture we wrote."
    )
    out.append("")

    if card.policy_digest:
        out.append("POLICY")
        out.append(f"    evidence: {EVIDENCE['POLICY']}")
        out.append(
            f"    digest {card.policy_digest[:16]}   {card.gated_tools} tool(s), "
            f"{card.gated_params} gated parameter(s)"
        )
        if card.params_without_ceiling:
            out.append(
                f"    {card.params_without_ceiling} parameter(s) have no ceiling declared: they "
                "resolve and record, but cannot block"
            )
        out.append("")

    out.append("KNOWN BLIND SPOTS (SCOPE.md)")
    out.append(f"    evidence: {EVIDENCE['BLIND SPOTS']}")
    for nc, text in NON_COVERAGE.items():
        out.append(f"    {nc}  {text}")
    out.append("")

    out.append("NOT YET MEASURED")
    out.append(f"    evidence: {EVIDENCE['NOT YET MEASURED']}")
    for item in card.outstanding:
        out.append(f"    - {item}")
    return "\n".join(out)


def scorecard_json(card: Scorecard) -> str:
    payload: dict[str, Any] = {
        "incidents": {
            key: [
                asdict(i)
                | {
                    "unit": i.unit.value if i.unit else None,
                    "gated_unit": i.gated_unit.value if i.gated_unit else None,
                }
                for i in entries
            ]
            for key, entries in card.incidents.items()
        },
        "coverage": {"caught": card.covered, "total": card.total_incidents},
        "friction": asdict(card.friction) | {"interrupt_rate": card.friction.interrupt_rate},
        "policy": {
            "digest": card.policy_digest,
            "gated_tools": card.gated_tools,
            "gated_params": card.gated_params,
            "params_without_ceiling": card.params_without_ceiling,
        },
        "unresolved_parameters": card.unresolved,
        "wild": (
            None if card.wild is None else asdict(card.wild) | {"gated_rate": card.wild.gated_rate}
        ),
        "live_verified": {name: LIVE_VERIFIED.get(name) for name in sorted(RESOLVERS)},
        "known_blind_spots": NON_COVERAGE,
        "not_yet_measured": card.outstanding,
    }
    return json.dumps(payload, indent=2, default=str)
