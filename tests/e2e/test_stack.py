"""The stack survey: every layer an agent can reach, and what this machine can see of it.

The demo measured one layer, because the filesystem is the only resolver needing no credential.
That is a report on a fraction of the blast radius presented as the whole, and the fix is not to
measure more — it is to *list* everything and be explicit about which parts are dark.

Three states, and the third is the one that would be easiest and most damaging to omit:

- **listening** — a credential is present, the reach was measured
- **dark** — a resolver exists, the credential does not
- **no resolver** — nothing here watches that layer at all

A table showing only what `neti` covers invites a reader to conclude that everything absent is
safe. It is the opposite: the uncovered layers are the ones nothing is watching.

Every test controls the environment rather than reading the developer's, because a survey that
passes on a laptop with `gh` installed and fails in CI is a survey nobody trusts.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from neti.eval.stack import LAYERS, State, survey


@pytest.fixture
def bare(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A machine with no credentials of any kind, whatever the developer actually has."""
    for name in list(os.environ):
        if name.startswith(("NETI_", "AWS_", "GITHUB_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("neti.eval.stack.shutil.which", lambda _: None)
    monkeypatch.setattr("neti.eval.stack.Path.home", lambda: tmp_path / "home")
    return tmp_path


def by_name(rows: list) -> dict[str, object]:  # type: ignore[type-arg]
    return {row.layer.name: row for row in rows}


def test_every_layer_appears_whatever_the_machine_has(bare: Path) -> None:
    """The point of the table. A layer that vanished when its credential was missing would let a
    reader infer coverage from silence."""
    rows = survey(bare)

    assert len(rows) == len(LAYERS)
    assert {row.layer.name for row in rows} == {layer.name for layer in LAYERS}


def test_a_bare_machine_still_listens_to_what_needs_no_credential(bare: Path) -> None:
    rows = by_name(survey(bare))

    assert rows["filesystem"].state is State.LISTENING  # type: ignore[attr-defined]
    assert rows["infrastructure"].state is State.LISTENING  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("layer", "variable"),
    [
        ("source control", "NETI_GITHUB_TOKEN"),
        ("database", "NETI_DATABASE_URL"),
        ("object storage", "AWS_ACCESS_KEY_ID"),
        ("directory", "NETI_TENANT_ID"),
    ],
)
def test_a_layer_without_its_credential_is_dark_and_names_the_variable(
    layer: str, variable: str, bare: Path
) -> None:
    """Dark is a state, not an omission, and the note has to be the thing to type.

    An operator reading "dark" without the variable name has learned that something is missing and
    not what. The whole point of the survey is that every line is actionable.
    """
    row = by_name(survey(bare))[layer]

    assert row.state is State.DARK  # type: ignore[attr-defined]
    assert variable in row.note  # type: ignore[attr-defined]


@pytest.mark.parametrize("layer", ["shell", "messaging", "SaaS records"])
def test_the_layers_with_no_resolver_are_listed_too(layer: str, bare: Path) -> None:
    """The honest half. These are where an agent acts and nothing here measures it — naming them
    beside the numbers is what stops the table reading as a clean bill of health."""
    row = by_name(survey(bare))[layer]

    assert row.state is State.UNCOVERED  # type: ignore[attr-defined]
    assert row.layer.what, "an uncovered layer still has to say what it is"  # type: ignore[attr-defined]


def test_the_shell_row_carries_its_reasoning(bare: Path) -> None:
    """`Bash` is the most consequential gap and the one most likely to be read as an oversight.

    It is a decision: sizing it means parsing a shell command to work out what `rm -rf "$X/../.."`
    removes, which is a gate guessing at a string rather than reading a value.
    """
    row = by_name(survey(bare))["shell"]

    assert "NC-09" in row.layer.what and "NC-10" in row.layer.what  # type: ignore[attr-defined]


def test_a_credential_lights_a_layer_up(bare: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETI_DATABASE_URL", "sqlite:///x.db")
    assert by_name(survey(bare))["database"].state is State.LISTENING  # type: ignore[attr-defined]


def test_entra_needs_all_three_of_its_variables(
    bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tenant id alone cannot authenticate, so a partial credential is still dark — reporting it
    as listening would promise a measurement that fails at the first call."""
    monkeypatch.setenv("NETI_TENANT_ID", "t")
    assert by_name(survey(bare))["directory"].state is State.DARK  # type: ignore[attr-defined]

    monkeypatch.setenv("NETI_CLIENT_ID", "c")
    monkeypatch.setenv("NETI_CLIENT_SECRET", "s")
    assert by_name(survey(bare))["directory"].state is State.LISTENING  # type: ignore[attr-defined]


def test_github_is_found_through_the_gh_cli_too(
    bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Most developers have `gh` authenticated and no `NETI_GITHUB_TOKEN` exported. Reading its
    token is a local keychain call, so it stays as cheap as reading a variable."""
    import subprocess

    monkeypatch.setattr("neti.eval.stack.shutil.which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        "neti.eval.stack.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "gho_token\n", ""),
    )
    assert by_name(survey(bare))["source control"].state is State.LISTENING  # type: ignore[attr-defined]


def test_a_broken_gh_is_not_a_credential(bare: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`gh` present but logged out. Treating a non-zero exit as success would report a layer as
    listening that cannot resolve a single call."""
    import subprocess

    monkeypatch.setattr("neti.eval.stack.shutil.which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        "neti.eval.stack.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "not logged in"),
    )
    assert by_name(survey(bare))["source control"].state is State.DARK  # type: ignore[attr-defined]


def test_every_covered_layer_names_a_resolver_that_exists() -> None:
    """The table is a claim about what ships. A layer naming a resolver nobody registered would
    promise coverage that is not there — the same failure the engine's construction guards catch
    for policies."""
    from neti.eval.synthetic import default_tenant
    from neti.resolvers.graph_client import ClientCredential, GraphClient
    from neti.resolvers.registry import resolvers_for_client

    client = GraphClient(
        ClientCredential(tenant_id="d", client_id="d", client_secret="d"),
        transport=default_tenant().transport(),
    )
    registered = set(resolvers_for_client(client))

    for layer in LAYERS:
        if layer.covered:
            assert layer.resolver in registered, (
                f"{layer.name} names a resolver that does not exist"
            )
        else:
            assert not layer.resolver, f"{layer.name} is marked uncovered but names one"


def test_the_demo_reports_the_whole_stack(bare: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end through `run_here`, which is what the command prints."""
    from neti.eval.here import run_here

    repo = bare / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x", encoding="utf-8")

    example = Path(__file__).resolve().parents[2] / "examples" / "coding-agent.yaml"
    result = run_here(repo, example)

    assert len(result.stack) == len(LAYERS)
    assert result.listening >= 1
    assert result.dark >= 1, "a bare machine must show dark layers rather than hiding them"
    filesystem = by_name(result.stack)["filesystem"]
    assert filesystem.reach == 1  # type: ignore[attr-defined]
