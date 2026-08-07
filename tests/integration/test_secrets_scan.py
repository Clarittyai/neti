"""Which `sensitive:` rules are worth declaring here — and only the ones that are.

`sensitive:` shipped commented out in the example policy and mentioned in a changelog, which is the
same as not shipping it. A capability nobody can find is a capability nobody has, and this
repository has caught itself doing that four times now.

The rule this follows is `insight/targets.py`'s, because it is the same rule: **real, or absent.**
Offering `**/*.pem` to a repository with no certificate in it is a rule that can never fire — dead
config that reads as configured.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neti.insight.secrets_scan import render, scan


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x", encoding="utf-8")
    return tmp_path


def matches(root: Path) -> set[str]:
    return {c.match for c in scan(root)}


def test_it_proposes_nothing_for_a_clean_tree(tree: Path) -> None:
    """The whole discipline. A repository with no secrets in it gets no rules, and `neti start`
    prints nothing rather than a section that reads as configured."""
    assert scan(tree) == []
    assert render([]) == ""


def test_it_finds_what_is_really_there(tree: Path) -> None:
    (tree / ".env").write_text("KEY=x", encoding="utf-8")
    assert matches(tree) == {"**/.env*"}

    (tree / "server.pem").write_text("k", encoding="utf-8")
    assert matches(tree) == {"**/.env*", "**/*.pem"}


def test_it_never_proposes_a_rule_that_could_not_fire(tree: Path) -> None:
    """The failure this exists to avoid, stated as a test: a `.env` in the tree must not drag in a
    rule about `.pem` files that are not."""
    (tree / ".env").write_text("KEY=x", encoding="utf-8")
    proposed = matches(tree)

    assert "**/*.pem" not in proposed
    assert "**/.ssh/**" not in proposed
    assert "**/secrets/**" not in proposed


def test_a_directory_counts_without_being_walked(tree: Path) -> None:
    """`.git` is thousands of files and the rule is about the directory. Descending it to decide
    would make `neti start` slow on exactly the repositories people run it in."""
    git = tree / ".git" / "objects" / "ab"
    git.mkdir(parents=True)
    for i in range(50):
        (git / f"obj{i}").write_text("x", encoding="utf-8")

    assert "**/.git/**" in matches(tree)


def test_every_candidate_names_the_thing_that_justifies_it(tree: Path) -> None:
    """A rule with a real example beside it is one somebody accepts or rejects on evidence."""
    (tree / ".env").write_text("KEY=x", encoding="utf-8")
    candidate = scan(tree)[0]

    assert candidate.example == ".env"
    assert candidate.why
    assert candidate.verdict in {"confirm", "block"}


def test_the_fragment_is_valid_policy(tree: Path) -> None:
    """It is text somebody pastes. If it does not load, the command has wasted their time and
    taught them the feature is broken."""
    import yaml

    from neti.config.policy import Policy, _normalise

    (tree / ".env").write_text("KEY=x", encoding="utf-8")
    (tree / "k.pem").write_text("k", encoding="utf-8")

    fragment = render(scan(tree))
    body = fragment[fragment.index("sensitive:") :]
    policy = Policy.model_validate(_normalise({**yaml.safe_load(body), "version": 1, "tools": {}}))

    assert len(policy.sensitive) == 2
    assert policy.sensitive[0].match == "**/.env*"
    assert policy.sensitive[0].why


def test_it_does_not_read_file_contents() -> None:
    """This is not a secret scanner, and one that read your files to tell you about them is a
    harder thing to trust than one that looks at their names. Asserted so it stays that way."""
    from tests.support import code_of

    source = code_of(Path("src/neti/insight/secrets_scan.py"))

    assert "read_text" not in source
    assert "open(" not in source
