"""The `providers:` block, which was in the policy schema and read by nothing.

An operator could write

    providers:
      fs: { root: /srv }

commit it, and get precisely the behaviour of having written nothing at all — no error, because
pydantic accepted the shape, and no effect, because no code path ever looked. That is the same
silent-dead-config failure the engine already has three guards for, sitting unnoticed in the schema
since the first release.

It matters most for `reachable_max`, which is what `neti inventory` reports on day one with no
traffic at all. Without a declared root, `fs.paths` correctly declines — "every file this process
can read" is not a bound — so the inventory prints `?` for every resolver outside Entra. Declaring
one is what turns that table into a finding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neti.config.policy import Policy
from neti.core.units import Direction, Unit
from neti.core.verdict import ResolutionState
from neti.engine import Engine
from neti.eval.synthetic import default_tenant
from neti.insight.inventory import build_inventory
from neti.resolvers.base import ResolveContext
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import PROVIDER_OPTIONS, resolvers_for_client

CRED = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")


def registry(providers: dict[str, dict[str, object]] | None = None) -> dict[str, object]:
    client = GraphClient(CRED, transport=default_tenant().transport())
    return dict(resolvers_for_client(client, providers))


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    for i in range(12):
        (root / "src" / f"f{i}.py").write_text("x", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------- it reaches resolvers


def test_a_declared_root_turns_the_inventory_from_a_question_mark_into_a_number(
    tree: Path,
) -> None:
    """The day-one finding, and the only reason this wiring exists."""
    resolvers = registry({"fs": {"root": str(tree)}})
    out = resolvers["fs.paths"].reachable_max(ResolveContext())  # type: ignore[attr-defined]

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == 12
    assert out.unit is Unit.OBJECTS


def test_without_a_root_it_still_declines_rather_than_guessing(tree: Path) -> None:
    """The behaviour that was there before, and must stay: an undeclared bound is not zero, and it
    is not "everything this process can read" either. It is unknown, and says so."""
    out = registry()["fs.paths"].reachable_max(ResolveContext())  # type: ignore[attr-defined]

    assert out.state is ResolutionState.UNRESOLVED
    assert out.evidence["reason"] == "no_root_declared"


def test_a_declared_cap_reaches_the_resolver(tree: Path) -> None:
    """The cap is a latency control, so it has to be tunable by whoever feels the latency."""
    resolvers = registry({"fs": {"root": str(tree), "cap": 5}})
    out = resolvers["fs.paths"].reachable_max(ResolveContext())  # type: ignore[attr-defined]

    assert out.magnitude == 5
    assert out.direction is Direction.LOWER_BOUND, (
        "a capped answer is a floor, whatever set the cap"
    )


def test_a_declared_github_owner_reaches_the_resolver() -> None:
    from neti.resolvers.github import GitHubReposResolver
    from tests.integration.test_github_resolver import FakeApi

    api = FakeApi({"/orgs/acme": {"public_repos": 40, "total_private_repos": 212}})
    out = GitHubReposResolver(api, owner="acme").reachable_max(ResolveContext())

    assert out.magnitude == 252
    assert out.unit is Unit.REPOSITORIES


def test_declared_reachable_hints_reach_db_and_storage() -> None:
    resolvers = registry(
        {"db": {"reachable_rows": 4_000_000}, "storage": {"reachable_objects": 90}}
    )

    rows = resolvers["db.rows"].reachable_max(ResolveContext())  # type: ignore[attr-defined]
    objects = resolvers["storage.objects"].reachable_max(ResolveContext())  # type: ignore[attr-defined]

    assert rows.magnitude == 4_000_000
    assert objects.magnitude == 90
    # Both are operator-declared ceilings on capability, so they can only ever be upper bounds.
    assert rows.direction is Direction.UPPER_BOUND
    assert objects.direction is Direction.UPPER_BOUND


def test_the_inventory_reports_it_end_to_end(tree: Path) -> None:
    """Through `build_inventory`, which is what the command and the console both call."""
    policy = Policy.model_validate(
        {
            "version": 1,
            "providers": {"fs": {"root": str(tree)}},
            "tools": {"Glob": {"gate": {"/pattern": {"resolver": "fs.paths"}}}},
        }
    )
    rows = build_inventory(policy, registry(policy.providers), ResolveContext())  # type: ignore[arg-type]

    assert len(rows) == 1
    assert rows[0].reachable.magnitude == 12


# ---------------------------------------------------------------------------- and it is guarded


def policy_with(providers: dict[str, dict[str, object]]) -> Policy:
    return Policy.model_validate({"version": 1, "providers": providers, "tools": {}})


@pytest.mark.parametrize(
    ("providers", "expected"),
    [
        ({"filesystem": {"root": "."}}, "no such provider"),
        ({"fs": {"roots": "."}}, "not an option"),
        ({"fs": {"root": ".", "depth": 3}}, "not an option"),
        ({"github": {"org": "acme"}}, "not an option"),
    ],
)
def test_a_provider_block_nobody_reads_is_refused_at_construction(
    providers: dict[str, dict[str, object]], expected: str
) -> None:
    """Near-misses, which is what these mistakes always are.

    `providers.filesystem.root` and `providers.fs.roots` both leave the inventory reporting `?`
    while looking configured — the exact failure mode that let `providers:` sit unread for the whole
    life of the project. Refused where the operator who typed it is still at the keyboard.
    """
    with pytest.raises(ValueError, match=expected):
        Engine(policy=policy_with(providers), resolvers=registry())  # type: ignore[arg-type]


def test_every_documented_provider_is_accepted() -> None:
    """The other direction: the guard must not reject configuration the registry actually reads."""
    for name, options in PROVIDER_OPTIONS.items():
        block = {option: "1" for option in options}
        Engine(policy=policy_with({name: block}), resolvers=registry())  # type: ignore[arg-type]


def test_the_example_policy_still_constructs() -> None:
    """`examples/entra.yaml` declares a `providers.entra` block that has always been decorative.

    It must keep loading — the guard is there to catch typos, not to invalidate the shipped example
    on the day the field became real.
    """
    from neti.config.policy import load_policy
    from tests.integration.test_inventory import EXAMPLE

    policy = load_policy(EXAMPLE)
    assert policy.providers, "the example is the reason this test exists"
    Engine(policy=policy, resolvers=registry(policy.providers))  # type: ignore[arg-type]
