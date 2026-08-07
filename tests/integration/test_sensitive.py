"""Dangerous because of *what it is*, not how much of it there is.

`SCOPE.md` has said this from the beginning and had no answer for it:

    NC-02  Correctness of the action. Deleting the one row that mattered.
           Magnitude is the wrong primitive. A cardinality of 1 is always under every ceiling.
    NC-05  Low-cardinality but high-consequence targets. Revoking one admin's access.
           Consequence is not cardinality.

`.env` is one object. `.git/` is one directory. No ceiling anybody would write reaches either, and
tuning the numbers never will — that is what "the wrong primitive" means.

So: a second comparison, on identity rather than count, declared in exactly the same way as the
first. Still a static match against a list a person committed; nothing inferred, nothing learned,
and the verdict still replays from the record. It **joins** with the magnitude verdict rather than
replacing it, so a rule written badly costs a confirmation and never a silent allow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from neti.config.policy import Policy
from neti.core.types import ProposedCall
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.resolvers.filesystem import FilesystemResolver


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    for i in range(5):
        (tmp_path / "src" / f"m{i}.py").write_text("x", encoding="utf-8")
    (tmp_path / ".env").write_text("STRIPE_KEY=sk_live", encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "prod.pem").write_text("key", encoding="utf-8")
    return tmp_path


def engine_for(tree: Path, sensitive: list[dict[str, Any]] | None = None) -> Engine:
    policy = Policy.model_validate(
        {
            "version": 1,
            "mode": Mode.ENFORCE,
            "sensitive": sensitive or [],
            "tools": {
                "delete": {
                    "gate": {
                        "/path": {
                            "resolver": "fs.paths",
                            "bands": [{"above": 100, "verdict": "block"}],
                            "on_unresolved": "block",
                        }
                    }
                }
            },
        }
    )
    return Engine(policy=policy, resolvers={"fs.paths": FilesystemResolver(root=tree)})


def fire(engine: Engine, target: str) -> Any:
    return engine.gate(ProposedCall(tool="delete", args={"path": target}, session_id="s"))


# --------------------------------------------------------------------------- the gap it closes


def test_one_object_can_now_be_stopped(tree: Path) -> None:
    """The whole point. `.env` is one file — under every ceiling, forever."""
    naive = engine_for(tree)
    assert fire(naive, str(tree / ".env")).decision.verdict.name == "ALLOW"

    guarded = engine_for(tree, [{"match": "**/.env", "verdict": "confirm", "why": "credentials"}])
    assert fire(guarded, str(tree / ".env")).decision.verdict.name == "CONFIRM"


def test_a_directory_rule_covers_what_is_under_it(tree: Path) -> None:
    guarded = engine_for(tree, [{"match": "**/secrets/**", "verdict": "block"}])
    assert fire(guarded, str(tree / "secrets" / "prod.pem")).decision.verdict.name == "BLOCK"


def test_ordinary_targets_are_untouched(tree: Path) -> None:
    """The cost of the feature, and it has to be nothing for work that never goes near a secret."""
    guarded = engine_for(tree, [{"match": "**/.env", "verdict": "block"}])
    assert fire(guarded, str(tree / "src")).decision.verdict.name == "ALLOW"


# --------------------------------------------------------------------------- the properties


def test_it_joins_and_never_rescues(tree: Path) -> None:
    """A sensitivity rule of `allow` must not be able to lower a verdict the magnitude bands
    already reached. Joined, not substituted — which is what makes a badly written rule cost a
    confirmation rather than an unnoticed pass."""
    for i in range(200):
        (tree / "src" / f"extra{i}.py").write_text("x", encoding="utf-8")

    guarded = engine_for(tree, [{"match": "**/src/**", "verdict": "allow"}])
    assert fire(guarded, str(tree / "src")).decision.verdict.name == "BLOCK"


def test_the_first_matching_rule_wins(tree: Path) -> None:
    """The operator wrote them in an order, and a list read top-down is one they can reason
    about."""
    guarded = engine_for(
        tree,
        [
            {"match": "**/.env", "verdict": "confirm", "why": "first"},
            {"match": "**/.env", "verdict": "block", "why": "second"},
        ],
    )
    result = fire(guarded, str(tree / ".env"))
    assert result.decision.verdict.name == "CONFIRM"
    assert result.record.sensitive[0]["why"] == "first"


def test_nothing_declared_changes_nothing(tree: Path) -> None:
    """Additive: a policy written before this behaves exactly as it did."""
    assert fire(engine_for(tree), str(tree / ".env")).decision.verdict.name == "ALLOW"


# --------------------------------------------------------------------------- the record says why


def test_the_record_names_the_rule_that_fired(tree: Path) -> None:
    """A block with no reason is a block somebody disables. `credentials live here` is one they
    work around correctly."""
    guarded = engine_for(
        tree, [{"match": "**/.env", "verdict": "block", "why": "credentials live here"}]
    )
    result = fire(guarded, str(tree / ".env"))

    assert result.record.verdict == "block"
    assert "sensitive" in result.record.rule
    assert result.record.sensitive[0]["match"] == "**/.env"
    assert result.record.sensitive[0]["why"] == "credentials live here"


def test_the_rule_names_magnitude_when_magnitude_decided(tree: Path) -> None:
    """Both axes can fire at once, and the record has to say which one actually decided — otherwise
    an operator tuning the wrong number wonders why nothing changes."""
    for i in range(200):
        (tree / "src" / f"extra{i}.py").write_text("x", encoding="utf-8")

    guarded = engine_for(tree, [{"match": "**/src/**", "verdict": "confirm"}])
    result = fire(guarded, str(tree / "src"))

    assert result.decision.verdict.name == "BLOCK"
    assert "magnitude" in result.record.rule, "the ceiling decided; the sensitivity rule only asked"
