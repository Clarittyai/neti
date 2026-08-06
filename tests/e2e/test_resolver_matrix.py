"""Every shipped resolver, driven all the way through the stack it is actually used in.

Each resolver has its own test file, and each of those stops at the resolver's own return value. So
nine resolvers are well tested at producing a `Resolution`, and almost nothing checks what happens
to that `Resolution` afterwards — through `Engine`, through `decide`, into a sealed record, out
through `report`, and back through `verify`.

That gap is where the interesting failures live, because the properties this product sells are not
properties of a resolver. They are properties of the path:

1. **The magnitude survives.** What the resolver measured is what the record says and what the
   report shows. A number that is right at the source and wrong three hops later is worse than no
   number, because the record is the artefact an auditor reads.
2. **`UNRESOLVED` never becomes `0`.** Every resolver is careful about this individually. The check
   here is that the *path* preserves it — an unreachable target and an empty one must not converge
   on the same verdict somewhere downstream.
3. **A `LOWER_BOUND` under the ceiling escalates rather than passing.** Four of the nine resolvers
   depend on this — it is the entire reason their caps and estimates are safe — and until now only
   `db.rows` had a test for it, in its own file, against its own resolver rather than against the
   decision procedure.

The table is the point: adding a resolver without adding a row leaves it outside all three.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from neti.config.policy import Policy
from neti.core.record import verify_chain
from neti.core.types import ProposedCall
from neti.core.units import Direction, Unit
from neti.core.verdict import ResolutionState
from neti.engine import Engine
from neti.eval.synthetic import default_tenant
from neti.gatekeeper import Gatekeeper
from neti.insight.report import build_report
from neti.resolvers.base import Resolver
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from neti.store.jsonl import JsonlSink, read_records

CRED = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")


@dataclass
class Fixture:
    """One resolver, with a target it can size and a target it cannot."""

    name: str
    unit: Unit
    sizeable: str
    """A target that resolves to `expect_at_least` or more."""

    expect_at_least: int
    unsizeable: str
    """A target that must come back UNRESOLVED — not zero."""

    bounded: bool = False
    """True when this resolver reports a bound rather than a count, so a magnitude under the ceiling
    must still escalate."""

    resolver: Resolver | None = None
    """Overrides the registry entry, for the resolvers that would otherwise need a cloud account."""


# ---------------------------------------------------------------------------- fixtures per resolver


def build_fixtures(tmp_path: Path) -> list[Fixture]:
    from neti.resolvers.database import DbapiCountRunner, RowsResolver
    from neti.resolvers.github import GitHubFilesResolver, GitHubReposResolver
    from neti.resolvers.shell import ShellPathsResolver
    from neti.resolvers.storage import ObjectStoreResolver
    from tests.integration.test_github_resolver import FakeApi, tree
    from tests.integration.test_storage_resolver import FakeLister

    # A real tree on disk, a real sqlite file. The local resolvers get real targets because they
    # can have them; only the networked ones are stood in for.
    tree_dir = tmp_path / "repo"
    tree_dir.mkdir()
    for i in range(120):
        (tree_dir / f"f{i}.txt").write_text("x" * 10, encoding="utf-8")

    db_path = tmp_path / "app.db"
    conn = sqlite3.connect(db_path)
    conn.execute("create table users (id integer primary key, org text)")
    conn.executemany("insert into users (org) values (?)", [("acme",)] * 400)
    conn.commit()

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "resource_changes": [
                    {"address": f"aws_instance.a{i}", "change": {"actions": ["delete"]}}
                    for i in range(7)
                ]
            }
        ),
        encoding="utf-8",
    )

    github = FakeApi(
        {
            "/orgs/acme": {"public_repos": 40, "total_private_repos": 212},
            "/repos/acme/api": {"default_branch": "main"},
            "/repos/acme/api/git/trees/main?recursive=1": tree(900),
        }
    )

    return [
        # --- the directory resolvers, against the synthetic tenant
        Fixture("entra.principals", Unit.PRINCIPALS, "g-eng-all", 41_203, "no-such-group"),
        Fixture("entra.apps", Unit.APPS, "g-eng-all", 1, "no-such-group"),
        Fixture("entra.guests", Unit.PRINCIPALS, "g-eng-all", 412, "no-such-group"),
        Fixture(
            "entra.principals_with_guests", Unit.PRINCIPALS, "g-eng-all", 41_203, "no-such-group"
        ),
        # --- the local ones, against real artefacts
        Fixture("fs.paths", Unit.OBJECTS, str(tree_dir), 120, str(tmp_path / "nowhere")),
        # A shell command rather than a path, because that is what this resolver is handed. The
        # unsizeable side is a real pipeline, not a nonsense string: `xargs rm` is the case the
        # parser declines by design, and it must decline it as UNRESOLVED rather than as zero.
        Fixture(
            "shell.paths",
            Unit.OBJECTS,
            f"rm -rf {tree_dir}",
            120,
            "cat list.txt | xargs rm",
            resolver=ShellPathsResolver(),
        ),
        Fixture("terraform.destroy", Unit.RESOURCES, str(plan), 7, str(tmp_path / "missing.json")),
        Fixture(
            "db.rows",
            Unit.ROWS,
            "DELETE FROM users WHERE org = 'acme'",
            400,
            "TRUNCATE TABLE users",
            bounded=True,
            resolver=RowsResolver(DbapiCountRunner(conn)),
        ),
        # --- the networked ones, against recorded providers
        Fixture(
            "storage.objects",
            Unit.OBJECTS,
            "s3://backups/prod/",
            1_200,
            "/not/an/s3/uri",
            resolver=ObjectStoreResolver(FakeLister(1_200)),
        ),
        Fixture(
            "github.repos",
            Unit.REPOSITORIES,
            "acme",
            252,
            "nobody-at-all",
            resolver=GitHubReposResolver(github),
        ),
        Fixture(
            "github.files",
            Unit.OBJECTS,
            "acme/api",
            900,
            "acme",
            resolver=GitHubFilesResolver(github),
        ),
    ]


@pytest.fixture
def fixtures(tmp_path: Path) -> list[Fixture]:
    return build_fixtures(tmp_path)


def resolvers_for(fixture: Fixture) -> dict[str, Resolver]:
    registry = resolvers_for_client(GraphClient(CRED, transport=default_tenant().transport()))
    if fixture.resolver is not None:
        registry[fixture.name] = fixture.resolver
    return registry


def engine_for(fixture: Fixture, *, ceiling: int, on_unbounded: str = "confirm") -> Engine:
    """A one-gate policy built around this resolver, so the whole stack runs for real."""
    policy = Policy.model_validate(
        {
            "version": 1,
            "mode": "enforce",
            "tools": {
                "act": {
                    "gate": {
                        "/target": {
                            "resolver": fixture.name,
                            "bands": [{"above": ceiling, "verdict": "block"}],
                            "on_unresolved": "block",
                            "on_unbounded": on_unbounded,
                        }
                    }
                }
            },
        }
    )
    return Engine(policy=policy, resolvers=resolvers_for(fixture))


def gate(engine: Engine, target: str, records: Path) -> Any:
    sink = JsonlSink(records)
    return Gatekeeper(engine=engine, sink=sink).decide(
        ProposedCall(tool="act", args={"target": target})
    )


def ids(fixtures: list[Fixture]) -> list[str]:
    return [f.name for f in fixtures]


# ---------------------------------------------------------------------------- 1. the number lands


@pytest.mark.parametrize("index", range(10))
def test_the_measured_magnitude_reaches_the_record_and_the_report(
    index: int, fixtures: list[Fixture], tmp_path: Path
) -> None:
    """From resolver to sealed record to `neti report`, unchanged.

    The record is the artefact an auditor reads and the only evidence that survives the process. A
    magnitude that is right at the source and wrong three hops later is worse than no magnitude.
    """
    fixture = fixtures[index]
    records = tmp_path / f"{fixture.name}.ndjson"
    # A ceiling far below the target, so this blocks and the magnitude is definitely exercised.
    decision = gate(engine_for(fixture, ceiling=0), fixture.sizeable, records)

    cause = decision.record.causes[0]
    assert cause["unit"] == fixture.unit.value
    magnitude = cause["magnitude"]
    assert magnitude is not None and magnitude >= fixture.expect_at_least, (
        f"{fixture.name} resolved {magnitude}, expected at least {fixture.expect_at_least}"
    )

    summary = build_report(read_records(records))
    dist = summary.distributions[("act", "/target")]
    assert dist.magnitudes == [magnitude], "the report must show what the record stored"
    assert dist.unit == fixture.unit.value

    ok, bad = verify_chain(read_records(records))
    assert ok, f"chain broke at {bad}"


# ---------------------------------------------------------------------------- 2. unresolved ≠ zero


@pytest.mark.parametrize("index", range(10))
def test_a_target_it_cannot_size_never_becomes_zero(
    index: int, fixtures: list[Fixture], tmp_path: Path
) -> None:
    """The invariant every resolver protects individually — checked here on the *path*.

    An unreachable target and an empty one are the same number and opposite situations. If they
    converge anywhere between the resolver and the verdict, the safest-looking call in the system
    is the one nothing could measure.
    """
    fixture = fixtures[index]
    records = tmp_path / f"{fixture.name}-unresolved.ndjson"
    # A ceiling high enough that a magnitude of 0 would sail through. Only `on_unresolved` can stop
    # this call, which is exactly what is being tested.
    decision = gate(engine_for(fixture, ceiling=1_000_000), fixture.unsizeable, records)

    cause = decision.record.causes[0]
    assert cause["magnitude"] is None, (
        f"{fixture.name} produced a number for a target it cannot size — "
        f"state={cause['state']}, magnitude={cause['magnitude']}"
    )
    assert cause["state"] != ResolutionState.RESOLVED.value
    assert decision.record.verdict == "block", "on_unresolved: block must decide it"

    summary = build_report(read_records(records))
    assert summary.distributions[("act", "/target")].unresolved == 1


# ---------------------------------------------------------------------------- 3. bounds escalate


def bound_producing(tmp_path: Path) -> list[Fixture]:
    """Every resolver that can report a floor, configured so that it does.

    Three of these only produce a bound when they hit their cap, so the caps are set to 1 here. That
    is the whole point: a cap is a latency control, and the thing that keeps it from also being a
    hole is that a capped answer escalates. Testing these at their real caps would need a
    200,000-file tree, so the cap moves instead of the data.
    """
    from neti.resolvers.database import DbapiCountRunner, RowsResolver
    from neti.resolvers.filesystem import FilesystemResolver
    from neti.resolvers.github import GitHubFilesResolver
    from neti.resolvers.storage import ObjectStoreResolver
    from tests.integration.test_github_resolver import FakeApi, tree
    from tests.integration.test_storage_resolver import FakeLister

    tree_dir = tmp_path / "capped"
    tree_dir.mkdir()
    for i in range(5):
        (tree_dir / f"f{i}.txt").write_text("x", encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    conn.execute("create table users (id integer primary key)")
    conn.executemany("insert into users (id) values (?)", [(i,) for i in range(3)])
    conn.commit()

    return [
        Fixture(
            "db.rows",
            Unit.ROWS,
            "DELETE FROM users",
            3,
            "TRUNCATE TABLE users",
            bounded=True,
            resolver=RowsResolver(DbapiCountRunner(conn)),
        ),
        Fixture(
            "fs.paths",
            Unit.OBJECTS,
            str(tree_dir),
            1,
            str(tmp_path / "nowhere"),
            bounded=True,
            resolver=FilesystemResolver(cap=1),
        ),
        Fixture(
            "storage.objects",
            Unit.OBJECTS,
            "s3://backups/prod/",
            1,
            "/not/an/s3/uri",
            bounded=True,
            resolver=ObjectStoreResolver(FakeLister(5_000), cap=1),
        ),
        Fixture(
            "github.files",
            Unit.OBJECTS,
            "acme/api",
            1,
            "acme",
            bounded=True,
            resolver=GitHubFilesResolver(
                FakeApi(
                    {
                        "/repos/acme/api": {"default_branch": "main"},
                        "/repos/acme/api/git/trees/main?recursive=1": tree(1, truncated=True),
                    }
                )
            ),
        ),
    ]


@pytest.mark.parametrize("index", range(4))
def test_a_bound_under_the_ceiling_escalates_instead_of_passing(index: int, tmp_path: Path) -> None:
    """The property four resolvers' safety rests on, checked through `decide` rather than asserted
    about a `Direction` in isolation.

    `db.rows` counts exactly what its predicate names and cannot see cascades; `fs.paths`,
    `storage.objects` and `github.files` stop at a cap. All four therefore report a floor, and a
    floor under a ceiling proves nothing. If the decision procedure let those through, the calls it
    understood least would be the ones it waved past — the cap would be a hole rather than a
    latency control.
    """
    fixture = bound_producing(tmp_path)[index]
    records = tmp_path / f"{fixture.name}-bound.ndjson"
    decision = gate(engine_for(fixture, ceiling=1_000_000), fixture.sizeable, records)

    cause = decision.record.causes[0]
    assert cause["magnitude"] is not None
    assert cause["magnitude"] < 1_000_000, "the fixture must sit under the ceiling to test anything"
    assert cause["direction"] == Direction.LOWER_BOUND.value, (
        f"{fixture.name} reported {cause['direction']} where a floor was expected"
    )
    assert decision.record.verdict == "confirm", (
        f"{fixture.name}: a lower bound under the ceiling took {decision.record.verdict}, "
        "but it must take on_unbounded — measuring under a ceiling proves nothing"
    )
    assert "on_unbounded" in cause["rule"]


def test_an_exact_resolution_under_the_ceiling_is_simply_allowed(tmp_path: Path) -> None:
    """The other half, so the rule above is not just "escalate everything".

    An EXACT measurement under the ceiling is conclusive and must pass, or the gate would interrupt
    on every ordinary call and be turned off within a day.
    """
    fixture = build_fixtures(tmp_path)[0]  # entra.principals, EXACT
    records = tmp_path / "exact.ndjson"
    decision = gate(engine_for(fixture, ceiling=1_000_000), fixture.sizeable, records)

    assert decision.record.causes[0]["direction"] == Direction.EXACT.value
    assert decision.record.verdict == "allow"


# ---------------------------------------------------------------------------- the table is complete


def test_every_registered_resolver_has_a_row(fixtures: list[Fixture]) -> None:
    """The table is only an invariant while it covers everything shipped.

    A resolver added to the registry without a row here would be outside all three checks above —
    which is precisely the position `storage.objects` and `github.*` were in before this file.
    """
    registered = set(
        resolvers_for_client(GraphClient(CRED, transport=default_tenant().transport()))
    )
    covered = {f.name for f in fixtures}
    assert registered == covered, (
        f"resolvers with no end-to-end row: {sorted(registered - covered)}; "
        f"rows for resolvers that do not exist: {sorted(covered - registered)}"
    )
