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


def test_the_strictest_matching_rule_wins(tree: Path) -> None:
    """**Reversed on 2026-08-10, and the old behaviour was a silent under-enforcement.**

    This used to assert that the *first* matching rule won, justified as "the operator wrote them in
    an order". That reading contradicted the invariant `decide` states two lines above the code that
    implemented it — *"Joined rather than substituted, so it raises a verdict and never lowers one"*
    — and the contradiction became reachable the moment rules could be scoped to tools. Written the
    way anybody would write them:

        - { match: "**/.env*", verdict: confirm, why: credentials live here }
        - { match: "**/.env*", tools: [Write, Edit], verdict: block, why: not recoverable }

    the broad rule matched first and `Write(.env)` came back CONFIRM. The stricter rule the operator
    had just declared never ran and nothing said so. Getting the intended verdict required ordering
    them narrowest-first, which is the opposite of how a list is read.

    What is given up is the ability to write a *weaker* exception before a broader rule. That is the
    correct thing to give up: every other axis here joins, and an axis that can lower a verdict is
    the one shape this product does not have.
    """
    guarded = engine_for(
        tree,
        [
            {"match": "**/.env", "verdict": "confirm", "why": "first"},
            {"match": "**/.env", "verdict": "block", "why": "second"},
        ],
    )
    result = fire(guarded, str(tree / ".env"))
    assert result.decision.verdict.name == "BLOCK"
    assert [s["why"] for s in result.record.sensitive] == ["first", "second"], (
        "both rules are recorded, so an auditor sees every reason rather than the winning one"
    )


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


# ------------------------------------------------- scoped to an operation, not only to a target


def scoped_engine(tree: Path, sensitive: list[dict[str, Any]]) -> Engine:
    """Two tools over the same files, so a rule can tell reading from overwriting."""
    policy = Policy.model_validate(
        {
            "version": 1,
            "mode": Mode.ENFORCE,
            "sensitive": sensitive,
            "tools": {
                "Read": {"gate": {"/file_path": {"resolver": "fs.paths"}}},
                "Write": {"gate": {"/file_path": {"resolver": "fs.paths"}}},
            },
        }
    )
    return Engine(policy=policy, resolvers={"fs.paths": FilesystemResolver(root=tree)})


def verdict_of(engine: Engine, tool: str, target: str) -> str:
    call = ProposedCall(tool=tool, args={"file_path": target}, session_id="s")
    return engine.gate(call).decision.verdict.name


def test_the_same_target_can_carry_two_consequences(tree: Path) -> None:
    """Reading `.env` is a confirm; overwriting it is not recoverable.

    That is not a statement about size — both are one object — and it could not be said at all
    before a rule could name the tools it applies to.
    """
    engine = scoped_engine(
        tree,
        [
            {"match": "**/.env", "verdict": "confirm", "why": "credentials live here"},
            {
                "match": "**/.env",
                "tools": ["Write", "Edit"],
                "verdict": "block",
                "why": "overwriting these is not recoverable",
            },
        ],
    )

    assert verdict_of(engine, "Read", str(tree / ".env")) == "CONFIRM"
    assert verdict_of(engine, "Write", str(tree / ".env")) == "BLOCK"


def test_a_rule_with_no_tools_still_applies_to_all_of_them(tree: Path) -> None:
    """Every rule written before scoping existed must keep meaning what it meant."""
    engine = scoped_engine(tree, [{"match": "**/.env", "verdict": "block", "why": "keys"}])

    assert verdict_of(engine, "Read", str(tree / ".env")) == "BLOCK"
    assert verdict_of(engine, "Write", str(tree / ".env")) == "BLOCK"


def test_a_tool_the_rule_does_not_name_is_untouched(tree: Path) -> None:
    engine = scoped_engine(
        tree,
        [{"match": "**/.env", "tools": ["Write"], "verdict": "block", "why": "not recoverable"}],
    )

    assert verdict_of(engine, "Read", str(tree / ".env")) == "ALLOW"
    assert verdict_of(engine, "Write", str(tree / ".env")) == "BLOCK"


# ------------------------------------------------- declared on the act, with nothing to measure


def test_an_operation_can_be_stopped_with_no_resolver_and_no_magnitude(tree: Path) -> None:
    """`SCOPE.md` NC-05: revoking one admin's access is one principal, under every ceiling.

    Until this, requiring a human for an irreversible operation meant inventing a resolver binding
    for it — and where none fitted, there was no way to say it at all. `delete_repository` is
    irreversible whatever it is pointed at, and that is a fact about the verb.
    """
    engine = scoped_engine(
        tree,
        [{"tools": ["delete_repository"], "verdict": "block", "why": "there is no undo"}],
    )

    result = engine.gate(ProposedCall(tool="delete_repository", args={"owner": "acme"}))
    assert result.decision.verdict.name == "BLOCK"
    assert result.decision.rule == "sensitive:tool:delete_repository"
    assert result.record.sensitive[0]["why"] == "there is no undo"


def test_a_whole_family_of_operations_in_one_line(tree: Path) -> None:
    """A federated server grows tools. Listing them by hand means one nobody remembered to add."""
    engine = scoped_engine(
        tree,
        [{"tools": ["mcp__github__delete_*"], "verdict": "confirm", "why": "destructive"}],
    )

    assert engine.gate(ProposedCall(tool="mcp__github__delete_branch")).decision.verdict.name == (
        "CONFIRM"
    )
    assert engine.gate(ProposedCall(tool="mcp__github__delete_repo")).decision.verdict.name == (
        "CONFIRM"
    )
    assert engine.gate(ProposedCall(tool="mcp__github__list_repos")).decision.verdict.name == (
        "ALLOW"
    )


def test_an_ungated_tool_nobody_named_is_still_out_of_scope(tree: Path) -> None:
    """The NC-09 exception is narrow on purpose.

    "An ungated tool is out of scope" means a tool *nobody mentioned*. One named in `sensitive:`
    has been mentioned, in the file that decides. Everything else still falls through.
    """
    engine = scoped_engine(
        tree,
        [{"tools": ["delete_repository"], "verdict": "block", "why": "there is no undo"}],
    )

    result = engine.gate(ProposedCall(tool="some_other_tool", args={"x": "1"}))
    assert result.decision.verdict.name == "ALLOW"
    assert result.decision.rule == "tool_not_gated"


# ------------------------------------------------- config that would have done nothing


def test_a_rule_that_names_neither_a_target_nor_a_tool_is_refused() -> None:
    """It would fire on every call there has ever been. Nobody means that."""
    with pytest.raises(ValueError, match="needs `match:`"):
        Policy.model_validate({"version": 1, "sensitive": [{"verdict": "block"}]})


def test_the_per_tool_sensitive_knob_that_never_worked_is_refused_by_name() -> None:
    """`sensitive: false` on a tool was in the schema and read by nothing.

    An operator could write it, commit it, and get exactly the behaviour of having written nothing.
    Rejected by name rather than as a generic extra field, because they believed something was
    switched off and deserve to be told what to write instead.
    """
    with pytest.raises(ValueError, match="never read by anything"):
        Policy.model_validate({"version": 1, "tools": {"Read": {"sensitive": False, "gate": {}}}})


def test_the_agent_is_not_told_to_retry_an_irreversible_verb_elsewhere(tree: Path) -> None:
    """The sentence has to cause the *right* retry, and for this rule there is no right retry.

    The general sensitivity denial ends "choose a different target", which for `delete_repository`
    is not merely unhelpful — an agent that follows it deletes a *different* repository. That is a
    worse outcome than the one that was blocked, produced by the gate's own instruction.
    """
    from neti.gateway.mcp import explain_denial

    engine = scoped_engine(
        tree,
        [{"tools": ["delete_repository"], "verdict": "block", "why": "there is no undo"}],
    )
    result = engine.gate(ProposedCall(tool="delete_repository", args={"owner": "acme"}))
    payload = {
        "sensitive": dict(result.decision.sensitive[0]),
        "parameter": "a parameter",
    }
    sentence = explain_denial(result, payload)

    assert "delete_repository" in sentence
    assert "there is no undo" in sentence
    assert "different target" not in sentence, "this instruction would cause a second deletion"
    assert "operator" in sentence, "the remedy is a person, so the sentence has to say so"
