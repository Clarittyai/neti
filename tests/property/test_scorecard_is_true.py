"""The scorecard's own copied lists, pinned to the things they claim to describe.

`src/neti/eval/scorecard.py` carries two hand-written tables — every registered resolver, and which
of them has been run against a real provider — and both are second copies of facts that live
somewhere else. That is exactly how `NC-10` went stale: SCOPE.md was rewritten, the scorecard's copy
was not, and the test that was supposed to catch it only compared the ids.

So each list is checked against its source rather than against another list:

- `RESOLVERS` against the registry that actually builds them.
- `LIVE_VERIFIED` against the presence of a `tests/live/` module that could have produced it.

The second is the load-bearing one. A scorecard row saying a resolver is verified against a real
provider, with no test anywhere that talks to one, is precisely the kind of claim this project
exists not to make.
"""

from __future__ import annotations

from pathlib import Path

from neti.eval.scorecard import LIVE_VERIFIED, RESOLVERS
from neti.eval.synthetic import default_tenant
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client

REPO = Path(__file__).resolve().parents[2]

# Which live module is entitled to make each claim. `fs.paths` has no provider to be live against —
# it walks a directory — so it is the one entry answered by the ordinary suite.
EVIDENCE = {
    "github.repos": "test_github_live.py",
    "github.files": "test_github_live.py",
    "db.rows": "test_postgres_live.py",
    "storage.objects": "test_s3_live.py",
    "terraform.destroy": "test_terraform_live.py",
    "fs.paths": None,
}


def _registered() -> set[str]:
    cred = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")
    client = GraphClient(cred, transport=default_tenant().transport())
    return set(resolvers_for_client(client))


def test_the_scorecard_lists_exactly_the_resolvers_that_ship() -> None:
    """Adding a resolver without adding it here would understate coverage; removing one and leaving
    it here would overstate it. Only the second matters, and it is the one nobody notices.
    """
    registered = _registered()
    assert set(RESOLVERS) == registered, (
        "scorecard.RESOLVERS has drifted from resolvers_for_client():\n"
        f"  only in the scorecard: {sorted(set(RESOLVERS) - registered)}\n"
        f"  only in the registry:  {sorted(registered - set(RESOLVERS))}"
    )


def test_every_live_verified_claim_names_a_resolver_that_exists() -> None:
    assert set(LIVE_VERIFIED) <= set(RESOLVERS), (
        f"claims a live check for something unregistered: {sorted(set(LIVE_VERIFIED) - RESOLVERS)}"
    )


def test_every_live_verified_claim_has_a_test_that_could_have_produced_it() -> None:
    """The claim and the evidence, checked against each other.

    `[verified] against api.github.com` is a sentence a reader will take at face value. It is only
    allowed to appear if a module exists that talks to that provider.
    """
    missing: list[str] = []
    for resolver in sorted(LIVE_VERIFIED):
        module = EVIDENCE.get(resolver, "")
        if module is None:
            continue  # local; no provider to reach
        if not module:
            missing.append(f"{resolver} is claimed verified but EVIDENCE names no module")
        elif not (REPO / "tests" / "live" / module).exists():
            missing.append(f"{resolver} claims {module}, which does not exist")

    assert not missing, "\n  ".join(["a verified claim with no evidence behind it:", *missing])


def test_an_unverified_resolver_is_not_quietly_dropped() -> None:
    """The Entra family is the whole unverified remainder, and it must keep showing as such.

    `CHANGELOG.md` says the Entra claims are unverified against a real tenant, and `neti check` and
    `neti measure` are written and waiting for one. If somebody adds an Entra row to `LIVE_VERIFIED`
    without a `tests/live/` module, the test above fails; if somebody deletes the resolvers instead,
    this one does.
    """
    entra = {r for r in RESOLVERS if r.startswith("entra.")}
    assert entra, "the Entra resolvers are the product's wedge; they cannot vanish silently"
    assert not (entra & set(LIVE_VERIFIED)), (
        "an Entra resolver is claimed live-verified. No tenant has ever been available: see "
        "scorecard outstanding items M2 and R2."
    )


# ---------------------------------------------------------------------------- M8, the seams


def test_every_shipped_adapter_appears_on_the_card() -> None:
    """The same anti-staleness mechanism as `RESOLVERS`, one axis over.

    `SEAMS` is a hand-written list on a card that claims eleven runtimes are covered, and a card is
    exactly where such a list rots — an adapter lands, the docstring is not updated, and the number
    reads high for a release. Every module under `neti.adapters` must have a row, and the four seams
    that are not adapters (both MCP transports and the in-process gate) are named explicitly rather
    than being an unexplained arithmetic difference.
    """
    import pkgutil

    import neti.adapters
    from neti.eval.scorecard import SEAMS

    shipped = {name for _, name, _ in pkgutil.iter_modules(neti.adapters.__path__)}
    # Adapter module -> the key it goes under on the card.
    by_module = {
        "claude_code": "hook",
        "tool_loop": "tool-loop",
        "anthropic_tools": "anthropic",
        "openai_agents": "openai-agents",
        "langchain_tools": "langchain",
        "crewai_hooks": "crewai",
        "pydantic_ai": "pydantic-ai",
        "autogen_tools": "autogen",
        "google_adk": "google-adk",
        "llamaindex_tools": "llamaindex",
        "smolagents_tools": "smolagents",
        "semantic_kernel_filters": "semantic-kernel",
    }
    assert shipped == set(by_module), (
        f"adapters with no scorecard row: {sorted(shipped - set(by_module))}. `neti score` would "
        "under-report which runtimes this can sit in front of."
    )
    assert set(by_module.values()) <= set(SEAMS)
    # The seams that are not adapter modules: the gate's two transports and the in-process gate.
    assert set(SEAMS) - set(by_module.values()) == {"mcp-stdio", "mcp-http", "preflight"}


def test_the_card_and_the_seam_table_agree_on_what_is_covered() -> None:
    """M8's claim is that the seams *agree*, and the thing that establishes it is one test table.

    So the card must not list a seam the table never drives. Read out of the table rather than
    restated, because a card claiming a runtime nothing exercises is the precise failure this
    metric exists to retire.
    """
    from neti.eval.scorecard import SEAMS
    from tests.e2e.test_seam_equivalence import SEAMS as DRIVEN

    assert set(SEAMS) == set(DRIVEN), (
        "the scorecard and the seam-equivalence table disagree about which doors are covered: "
        f"card only {sorted(set(SEAMS) - set(DRIVEN))}, "
        f"table only {sorted(set(DRIVEN) - set(SEAMS))}"
    )


def test_the_card_and_the_worlds_agree_on_the_resolver_families() -> None:
    """Same again for the other axis of the same table.

    Against `RESOLVER_WORLDS` specifically. `WORLDS` also carries `budget`, which exists for a
    *shape* — one file, resolving to 1, called twice — rather than for a resolver family, and
    counting it would inflate the coverage figure the card prints. The two lists had already
    drifted apart once by the time this was written.
    """
    from neti.eval.scorecard import _RESOLVER_FAMILIES
    from tests.e2e import worlds

    assert set(_RESOLVER_FAMILIES) == set(worlds.RESOLVER_WORLDS)


def test_every_world_is_accounted_for_as_one_kind_or_the_other() -> None:
    """No world may exist without being classified, or the count above silently stops being true."""
    from tests.e2e import worlds

    declared = set(worlds.POLICIES) | {"entra"}
    assert declared == set(worlds.WORLDS), (
        f"worlds with no classification: {sorted(declared ^ set(worlds.WORLDS))}"
    )
    assert not set(worlds.RESOLVER_WORLDS) & set(worlds.SHAPE_WORLDS)


# ---------------------------------------------------------------------------- the evidence rule


def test_every_section_of_the_card_names_evidence_a_reader_can_check() -> None:
    """`eval/README.md`'s rule, and its converse.

    *A trial that does not end as a number on `neti score` does not count* — and a number on
    `neti score` that nothing produced does not count either. A card is exactly where a claim
    outlives the thing that justified it: the metric stays, the survey stops being run, and nobody
    notices because the section still prints.

    So every heading the card emits must have an `EVIDENCE` entry, and every entry that names a path
    must name one that is there. Two directions, because either alone is a hole: an unbacked section
    is a claim with nothing behind it, and an orphaned entry is a rule nothing is applying.
    """
    import re

    from neti.eval.scorecard import EVIDENCE

    rendered = _full_card()
    # Headings are the unindented, upper-case lines the formatter emits.
    headings = [
        line
        for line in rendered.splitlines()
        if line and line[0].isalnum() and line == line.upper()
    ]
    assert headings, "no headings found — has the card's shape changed?"

    for heading in headings:
        key = next((k for k in EVIDENCE if heading.startswith(k)), None)
        assert key is not None, (
            f"the card prints {heading!r} and `EVIDENCE` says nothing backs it. Either name the "
            "artefact or stop printing the section."
        )

    for key, artefact in EVIDENCE.items():
        # Some evidence is a runtime input rather than a file in the tree — the record chain, the
        # policy — and those name themselves rather than a path.
        for path in re.findall(r"[\w./-]+\.(?:py|md)|(?:tests|eval|src)/[\w./-]+", artefact):
            assert (REPO / path).exists(), f"{key} cites {path}, which does not exist"


def test_the_card_says_evidence_for_each_section_out_loud() -> None:
    """It is only worth anything if a reader sees it.

    An `EVIDENCE` dict nobody prints is a convention; a line under every heading is a standing
    invitation to go and check, which is the whole posture this card is written in.
    """
    from neti.eval.scorecard import EVIDENCE

    rendered = _full_card()
    for artefact in EVIDENCE.values():
        assert f"evidence: {artefact}" in rendered, (
            f"the card never shows the evidence {artefact!r}"
        )


def _full_card() -> str:
    """Every section rendered at once.

    `POLICY` only appears when a policy was passed, `M10` only when a field survey was, and `M12`
    only when somebody has run `just assist`. A card built from nothing would let all three claim
    whatever they liked without either test above ever seeing them — which is what happened when
    M12 was added and this helper was not updated with it.
    """
    from neti.config.policy import load_policy
    from neti.eval.scorecard import Assist, Wild, build_scorecard, format_scorecard

    card = build_scorecard(
        None,
        load_policy(str(REPO / "examples" / "entra.yaml")),
        wild=Wild(
            servers_launched=13,
            servers_in_catalogue=22,
            tools_discovered=160,
            tools_gated=25,
            tools_sizable_in_principle=34,
        ),
        assist=Assist(
            model="a-model",
            provider="a-provider",
            of=39,
            recovered=31,
            wrong_resolver=2,
            missed=6,
            extra=4,
        ),
        conformance={
            "langgraph": {
                "status": "passed",
                "depth": "agent_loop",
                "what": "a compiled StateGraph",
                "version": "1.2.10",
            }
        },
    )
    return format_scorecard(card)


def test_every_runtime_the_card_names_arrives_through_a_seam_that_exists() -> None:
    """The list is only useful while every entry points somewhere real.

    A runtime naming a seam that is not in `SEAMS` would print a door that does not exist, and a
    seam no runtime names is a door nobody was told about — the second is the likelier mistake and
    the one that makes the product look narrower than it is.
    """
    from neti.eval.scorecard import RUNTIMES, SEAMS

    assert set(RUNTIMES.values()) <= set(SEAMS), (
        f"runtimes point at seams that do not exist: {sorted(set(RUNTIMES.values()) - set(SEAMS))}"
    )
    assert set(SEAMS) <= set(RUNTIMES.values()), (
        f"seams no runtime is listed against: {sorted(set(SEAMS) - set(RUNTIMES.values()))}"
    )


def test_the_runtime_list_keeps_driven_and_via_mcp_apart() -> None:
    """The distinction the whole table rests on.

    An adapter row was actually run by the seam table. An MCP client was not run at all — what is
    tested is that neti gates a real MCP server, and that Cursor speaks MCP is a fact about Cursor.
    Printing both as though each had been driven here is the overclaim `neti score` exists to avoid,
    so the two groups must stay separable: every non-MCP seam in the list has to be a shipped
    adapter or the in-process gate.
    """
    import pkgutil

    import neti.adapters
    from neti.eval.scorecard import RUNTIMES

    adapters = {name for _, name, _ in pkgutil.iter_modules(neti.adapters.__path__)}
    driven = {seam for seam in RUNTIMES.values() if not seam.startswith("mcp-")}

    # Each driven seam is either an adapter module or the in-process gate, which has no module.
    assert len(driven) == len(adapters) + 1, (
        f"{len(driven)} driven seams but {len(adapters)} adapters — a runtime is claimed as driven "
        "without an adapter behind it, or an adapter has no runtime listed."
    )
    assert "preflight" in driven


def test_the_card_says_what_it_does_not_reach() -> None:
    """A coverage table with no complement is a marketing table.

    Asserted as rendered output rather than as a constant, because the failure worth preventing is
    somebody keeping the list and quietly dropping the section that prints it.
    """
    from neti.eval.scorecard import NOT_REACHED

    assert NOT_REACHED, "the not-reached list is empty, which cannot be true"

    rendered = _full_card()
    assert "NOT reached:" in rendered

    # Compared after unwrapping, because the card hard-wraps these to width and a substring check
    # against the raw output would pass or fail on where the line breaks landed.
    flat = " ".join(rendered.split())
    for limit in NOT_REACHED:
        assert " ".join(limit.split()) in flat, (
            "a non-coverage entry is declared and never printed; the section that shows it has "
            "been dropped or truncated"
        )


def test_the_live_evidence_and_the_live_claims_name_the_same_modules() -> None:
    """Two lists again, and the same rule as everywhere else in this file.

    `tests/live/conftest.py` decides which resolver each module is evidence *for*; `EVIDENCE` above
    decides which module each claim points *at*. They are separate because one produces the file and
    the other reads it, and separate lists are how a coverage claim goes stale — a live module
    renamed on one side would quietly stop backing the claim on the other while both still looked
    populated.
    """
    from tests.live.conftest import PROVES

    provable = {resolver for resolvers in PROVES.values() for resolver in resolvers}
    claimed = {name for name, module in EVIDENCE.items() if module}

    # One direction only, and the asymmetry is the point. Every *claim* must have a module that
    # could produce it — a claim with no possible evidence is the defect this file exists for.
    assert claimed <= provable, (
        f"the card claims {sorted(claimed - provable)} live-verified; no module proves it"
    )

    # The other direction is legitimate and currently true: `test_entra_live.py` exists and nobody
    # here has a tenant, so those four resolvers are provable and correctly *not* claimed. A written
    # check nobody has run is a different state from no check at all, and both are honest.
    assert provable - claimed, (
        "every provable resolver is also claimed, which would mean the live tier has no module "
        "waiting on credentials — true one day, and worth noticing when it happens"
    )


def test_a_resolver_claimed_live_with_no_recorded_run_is_not_shown_as_verified() -> None:
    """The state the card could not previously express, and the one worth seeing.

    `LIVE_VERIFIED` is an assertion somebody wrote. Until now the card rendered it as `[verified]`
    whether or not anything had ever run — which is the shape of defect this whole file exists to
    catch, sitting inside the section that reports on evidence.
    """
    from neti.eval.scorecard import build_scorecard, format_scorecard

    none_run = format_scorecard(build_scorecard(None, None, live=None))
    assert "[claimed " in none_run, "a claim with no run behind it is still printed as verified"
    assert "no recorded run" in none_run

    confirmed = format_scorecard(
        build_scorecard(
            None,
            None,
            live={"db.rows": {"verified": True, "passed": 8, "module": "test_postgres_live.py"}},
        )
    )
    assert "db.rows" in confirmed
    assert "8 checks passed" in confirmed


# ---------------------------------------------------------------------------- M12


def _assist_card() -> str:
    from neti.eval.scorecard import Assist, build_scorecard, format_scorecard

    return format_scorecard(
        build_scorecard(
            assist=Assist(
                model="a-model",
                provider="a-provider",
                of=39,
                recovered=31,
                wrong_resolver=2,
                missed=6,
                extra=4,
            )
        )
    )


def test_m12_is_outstanding_until_somebody_runs_it() -> None:
    """It needs a key, so an absent result means "not run here", never "a model did badly".

    The same rule as M7 and M10, and the reason the card is worth reading at all: a metric that
    prints a number when nothing measured one is the failure this whole file exists to prevent.
    """
    from neti.eval.scorecard import build_scorecard, format_scorecard

    card = build_scorecard()
    assert card.assist is None
    assert any(item.startswith("M12") for item in card.outstanding)
    # Rendered from *this* card, not from `_full_card`, which deliberately supplies every optional
    # section so the evidence checks can see them.
    assert "M12 MODEL-ASSISTED" not in format_scorecard(card)


def test_m12_reports_what_the_model_got_wrong_before_what_it_got_right() -> None:
    """The order is the point, and it is the same rule the incident table follows.

    A recovery rate reads as an endorsement. The number that decides whether this feature is worth
    shipping is how often a model is confidently wrong, so that number comes first. Asserted on
    position rather than presence, because both numbers being *somewhere* is not the claim.
    """
    rendered = _assist_card()
    body = rendered[rendered.index("M12 MODEL-ASSISTED") :]
    assert body.index("got 2 wrong") < body.index("recovered 31")


def _arm_c_card() -> str:
    from neti.eval.scorecard import Assist, build_scorecard, format_scorecard

    return format_scorecard(
        build_scorecard(
            assist=Assist(
                model="a-model",
                provider="a-provider",
                of=39,
                recovered=31,
                wrong_resolver=2,
                missed=6,
                extra=4,
                unclaimed_of=388,
                unclaimed_found=7,
                unclaimed_false=92,
                unclaimed_forced=7,
                unclaimed_unadjudicated=13,
            )
        )
    )


def test_arm_c_reports_the_false_claims_before_the_finds() -> None:
    """The same rule again, and it bites hardest here.

    Arm C is the arm with a headline: *it found gates the rule table misses*. That sentence sells
    the feature, and on the evidence so far it is bought with fifteen wrong claims apiece. Reading
    order decides which of those two facts a person leaves with.
    """
    body = _arm_c_card()
    body = body[body.index("M12 MODEL-ASSISTED") :]
    assert body.index("92 thing(s) adjudicated not a set") < body.index("gate(s) the rule table")


def test_arm_c_says_its_answer_key_is_an_opinion() -> None:
    """Arms A and B are scored against the rule table's own committed output. Arm C is not.

    Its key is a reading somebody wrote down, and a number presented without that caveat is a
    number pretending to more authority than it has. The card names the file and invites the
    argument, because the alternative is a reader assuming a fact was measured.
    """
    body = _arm_c_card()
    assert "written reading, not a fact" in body
    assert "eval/answers/adjudicate.py" in body


def test_arm_c_reports_what_it_refused_to_adjudicate() -> None:
    """Excluding the hard cases is defensible. Excluding them silently is not."""
    assert "13 pair(s) are unadjudicated and scored nowhere" in _arm_c_card()


def test_the_card_stays_quiet_about_arm_c_when_nobody_ran_it() -> None:
    """An arm that was not run must not render as an arm that found nothing."""
    rendered = _assist_card()
    assert "no rule claims at all" not in rendered


def test_m12_says_out_loud_that_none_of_it_is_a_gate() -> None:
    """The card is where somebody's security team reads about this, so it says the posture there.

    A section describing a model's judgement, on a card about a gate, has to be unambiguous that the
    two never meet.
    """
    body = _assist_card()
    assert "Nothing here is a gate" in body
    assert "commented-out fragment" in body
    assert "cannot block" in body


def test_the_wrong_total_counts_every_way_of_being_wrong() -> None:
    """`wrong_resolver` alone would flatter it: a missed gate and an invented one both cost."""
    from neti.eval.scorecard import Assist

    assert Assist(of=39, recovered=31, wrong_resolver=2, missed=6, extra=4).wrong == 12


def test_m12_reports_the_over_claim_rate_when_arm_b_has_run() -> None:
    """The number that justifies the design, rather than the one that sells it.

    Arm A makes a model look trustworthy: shown parameters nobody has judged, it claimed nothing
    wrong. Arm B shows the other half — shown parameters that *look* claimable and were rejected in
    writing, it claims most of them. `neti suggest` never sends those, and this is the measurement
    of the appetite that exclusion exists to contain. A card that printed only arm A would be
    advertising the feature instead of describing it.
    """
    from neti.eval.scorecard import Assist, build_scorecard, format_scorecard

    body = format_scorecard(
        build_scorecard(
            assist=Assist(
                model="m", provider="p", of=34, recovered=30, contested=41, over_claimed=28
            )
        )
    )
    section = body[body.index("M12 MODEL-ASSISTED") :]
    assert "declined *with a written reason*, it claimed 28" in section
    assert "wrong by construction" in section
    assert "never sends them" in section


def test_m12_omits_the_over_claim_line_when_arm_b_has_not_run() -> None:
    """Arms are separable, and a missing one must not print as a zero.

    `--arm recovery` is a legitimate run. Rendering "claimed 0 of 0" from it would read as a
    perfect score for a measurement nobody took, which is the exact failure this file exists for.
    """
    from neti.eval.scorecard import Assist, build_scorecard, format_scorecard

    body = format_scorecard(
        build_scorecard(assist=Assist(model="m", provider="p", of=34, recovered=30))
    )
    section = body[body.index("M12 MODEL-ASSISTED") :]
    # Arm B's block only. "claimed 0" on its own would match arm A's `extra` count, which is a real
    # measurement and correctly zero — the two arms report different things and the test has to
    # know which one it is looking at.
    assert "written reason" not in section
    assert "wrong by construction" not in section
    assert "never sends them" not in section


# ---------------------------------------------------------------------------- M13


def test_m13_is_outstanding_until_somebody_runs_it() -> None:
    """An absent conformance file means "not run here", never "a runtime failed".

    The same rule as M7, M10, M11 and M12. It matters more here than most: this is the section a
    reader consults to find out whether their framework works, and a blank one must not be
    mistakable for a bad one.
    """
    from neti.eval.scorecard import build_scorecard, format_scorecard

    card = build_scorecard()
    assert card.conformance is None
    assert any(item.startswith("M13") for item in card.outstanding)
    assert "M13 RUNTIME CONFORMANCE" not in format_scorecard(card)


def test_m13_never_shows_an_absent_framework_as_driven() -> None:
    """`skipped` is a framework that is not installed, and it is a different fact from a pass.

    A matrix that renders both the same way is how "we tested with eight runtimes" becomes true of
    a machine where two of them were never present.
    """
    from neti.eval.scorecard import build_scorecard, format_scorecard

    rendered = format_scorecard(
        build_scorecard(
            conformance={
                "langgraph": {
                    "status": "passed",
                    "depth": "agent_loop",
                    "what": "a graph",
                    "version": "1.2.10",
                },
                "crewai": {
                    "status": "skipped",
                    "depth": "agent_loop",
                    "what": "a crew",
                    "version": "",
                },
            }
        )
    )
    body = rendered[rendered.index("M13 RUNTIME CONFORMANCE") :]
    assert "1 of 2 runtime(s) driven here" in body
    assert "[absent  ] crewai" in body
    assert "[driven  ] langgraph 1.2.10" in body


def test_m13_says_no_model_was_involved() -> None:
    """The whole claim. A reader who misses it will assume these rows cost tokens and a key."""
    from neti.eval.scorecard import build_scorecard, format_scorecard

    body = format_scorecard(
        build_scorecard(
            conformance={
                "langgraph": {
                    "status": "passed",
                    "depth": "agent_loop",
                    "what": "a graph",
                    "version": "1.2.10",
                }
            }
        )
    )
    assert "with no model at all" in body
    assert "scripts" in body


def test_m13_does_not_imply_a_real_model_was_ever_involved() -> None:
    """The live half is the easiest thing on this card to read as done when it is not.

    Every scripted row says `driven`, and a reader skimming eleven of them will take the section
    for a complete result. It is a complete result about the *seam*; it says nothing about a real
    provider until somebody spends a key. So the card says which of the two it is looking at.
    """
    from neti.eval.scorecard import build_scorecard, format_scorecard

    rows = {
        "langgraph": {
            "status": "passed",
            "depth": "agent_loop",
            "what": "a graph",
            "version": "1.2.10",
        }
    }
    scripted = format_scorecard(build_scorecard(conformance=rows))
    assert "Nobody has run it against a real model here" in scripted

    lived = format_scorecard(
        build_scorecard(
            conformance=rows,
            conformance_live={"anthropic": {"status": "passed", "model": "claude-opus-4-5"}},
        )
    )
    assert "With a real model behind it: anthropic" in lived
    assert "claude-opus-4-5" in lived
    assert "Nobody has run it" not in lived


def test_m13_never_counts_a_skipped_live_row_as_proved() -> None:
    """A row that skipped for want of a key is the whole reason the file records skips at all."""
    from neti.eval.scorecard import build_scorecard, format_scorecard

    body = format_scorecard(
        build_scorecard(
            conformance={
                "langgraph": {
                    "status": "passed",
                    "depth": "agent_loop",
                    "what": "a graph",
                    "version": "1.2.10",
                }
            },
            conformance_live={"anthropic": {"status": "skipped", "why": "no key"}},
        )
    )
    assert "Nobody has run it against a real model here" in body
    assert "With a real model behind it" not in body
