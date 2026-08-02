"""Every policy in `examples/` has to load, construct and gate.

`entra.yaml` is exercised incidentally by a dozen test files that import it as a fixture, so it
cannot rot. `coding-agent.yaml` has no such accidental coverage, and it is the one most people will
actually copy — a coding agent is what most agent users are running, and its policy is the one
`neti demo --here` is built around.

An example that does not load is worse than no example: it is the first thing a stranger tries, and
it fails in a way that reads as "this product is broken" rather than "this file is stale."
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neti.config.policy import Policy, load_policy
from neti.core.types import ProposedCall
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.synthetic import default_tenant
from neti.insight.inventory import build_inventory
from neti.resolvers.base import ResolveContext
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
CRED = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")


def shipped() -> list[Path]:
    found = sorted(EXAMPLES.glob("*.yaml"))
    assert found, "examples/ is empty — has the directory moved?"
    return found


def engine_for(policy: Policy) -> Engine:
    client = GraphClient(CRED, transport=default_tenant().transport())
    return Engine(policy=policy, resolvers=resolvers_for_client(client, policy.providers))


@pytest.mark.parametrize("path", shipped(), ids=lambda p: p.name)
def test_it_loads_and_constructs(path: Path) -> None:
    """Construction is where the four dead-config guards run, so this is not a formality: a stale
    resolver name, an impossible breakdown band or an unread provider key all fail right here."""
    engine_for(load_policy(path))


@pytest.mark.parametrize("path", shipped(), ids=lambda p: p.name)
def test_it_ships_in_observe_mode(path: Path) -> None:
    """The advice every example gives in its own header. An example that shipped enforcing would
    block a stranger's first call against ceilings they had no part in choosing."""
    assert load_policy(path).mode is Mode.OBSERVE


@pytest.mark.parametrize("path", shipped(), ids=lambda p: p.name)
def test_every_gated_parameter_resolves_or_says_why(path: Path) -> None:
    """`neti inventory` must produce a row per gated parameter and never raise."""
    policy = load_policy(path)
    engine = engine_for(policy)
    rows = build_inventory(policy, engine.resolvers, ResolveContext())

    expected = sum(len(policy.gate_specs(tool)) for tool in policy.tools)
    assert len(rows) == expected


def test_the_coding_agent_example_actually_gates_this_repository(tmp_path: Path) -> None:
    """The claim it exists to make, measured against a real tree rather than asserted.

    A `Glob` for everything under the root has to resolve to the file count, not to nothing — which
    is the whole difference between a policy that gates a coding agent and a page of `allow`.
    """
    tree = tmp_path / "repo"
    (tree / "src").mkdir(parents=True)
    for i in range(30):
        (tree / "src" / f"f{i}.py").write_text("x")

    policy = load_policy(EXAMPLES / "coding-agent.yaml").model_copy(
        update={"mode": Mode.ENFORCE, "providers": {"fs": {"root": str(tree)}}}
    )
    result = engine_for(policy).gate(ProposedCall(tool="Glob", args={"pattern": str(tree)}))

    cause = result.record.causes[0]
    assert cause["magnitude"] == 30, "the shipped policy must size a real directory"
    assert cause["unit"] == "objects"


def test_bash_is_ungated_on_purpose_and_says_so() -> None:
    """The most load-bearing omission in the file.

    Sizing `Bash` means parsing a shell command to work out what `rm -rf "$X/../.."` removes — a
    gate guessing at a string's meaning rather than reading a value. If somebody quietly adds it,
    the product starts making a weaker claim than it advertises, so the reasoning has to survive in
    the file next to the gap it explains.
    """
    text = (EXAMPLES / "coding-agent.yaml").read_text()
    policy = load_policy(EXAMPLES / "coding-agent.yaml")

    assert "Bash" not in policy.tools, "Bash must stay out of scope (NC-09)"
    assert "NC-09" in text and "NC-10" in text, "the reasoning must stay with the omission"
