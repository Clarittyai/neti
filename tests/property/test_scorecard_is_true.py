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
        "anthropic_tools": "anthropic",
        "openai_agents": "openai-agents",
        "langchain_tools": "langchain",
        "crewai_hooks": "crewai",
        "pydantic_ai": "pydantic-ai",
        "autogen_tools": "autogen",
        "google_adk": "google-adk",
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
    """Same again for the other axis of the same table."""
    from neti.eval.scorecard import _RESOLVER_FAMILIES
    from tests.e2e import worlds

    assert set(_RESOLVER_FAMILIES) == set(worlds.WORLDS)
