"""Which `sensitive:` rules are worth declaring here — and only the ones that are.

`sensitive:` shipped commented out in the example policy and mentioned in a changelog, which is the
same as not shipping it. A capability nobody can find is a capability nobody has, and this
repository has caught itself doing that four times now.

The rule this follows is `insight/targets.py`'s, because it is the same rule: **real, or absent.**
Offering `**/*.pem` to a repository with no certificate in it is a rule that can never fire — dead
config that reads as configured.

**And a committed test fixture is not real.** Running day zero on four cloned repositories —
`psf/requests`, `pallets/flask`, `expressjs/express`, `django/django` — proposed `**/*.pem` and
`**/*.key` to `requests` on the strength of seven files under `tests/certs/`, every one of them
published on GitHub so the TLS suite has something to hand a socket. The resulting rule interrupts
an agent each time it opens the test suite it was asked to work on, and protects nothing.

No synthetic tree produces that: every generated fixture in this suite has uniform invented files
in flat directories. It took real repositories to see, so the cases below are shaped like them.
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


# --------------------------------------------------------------------- committed sample data


def test_a_repository_whose_only_keys_are_test_fixtures_gets_no_rule(tmp_path: Path) -> None:
    """`psf/requests`, exactly as cloned: seven certificates, all under `tests/certs/`.

    They are in a public repository so the TLS suite has something to hand a socket. A rule written
    on their account fires every time somebody works on that suite and guards nothing — the noise
    that gets a control switched off, and the reason this is a scan-time decision rather than a
    verdict somebody has to tune away afterwards.
    """
    certs = tmp_path / "tests" / "certs" / "valid" / "server"
    certs.mkdir(parents=True)
    (certs / "server.pem").write_text("x", encoding="utf-8")
    (certs / "server.key").write_text("x", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "adapters.py").write_text("x", encoding="utf-8")

    assert scan(tmp_path) == []


def test_a_fixture_env_is_not_a_credential(tmp_path: Path) -> None:
    """`pallets/flask`, exactly as cloned: its only `.env` is `tests/test_apps/.env`."""
    apps = tmp_path / "tests" / "test_apps"
    apps.mkdir(parents=True)
    (apps / ".env").write_text("SECRET_KEY=config", encoding="utf-8")

    assert scan(tmp_path) == []


def test_a_real_key_beside_the_fixtures_still_earns_its_rule(tmp_path: Path) -> None:
    """The half that must not be lost. A fixture suppresses nothing on its own — it only fails to
    make the case by itself."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "server.pem").write_text("x", encoding="utf-8")
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "prod.pem").write_text("x", encoding="utf-8")

    found = scan(tmp_path)
    assert [c.match for c in found] == ["**/*.pem"]
    assert found[0].example == "deploy/prod.pem", (
        "the example beside a rule has to be the file that makes the case for it, "
        "not whichever one the walk reached first"
    )


def test_only_the_directory_matters_not_the_filename(tmp_path: Path) -> None:
    """`test_config.py` sitting in the project root is source, not sample data.

    Matching on the name would suppress a rule for `src/test_keys.pem`, which is a real file in a
    real place. The signal is the directory, and it is the directory this checks.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "test_server.pem").write_text("x", encoding="utf-8")

    assert [c.match for c in scan(tmp_path)] == ["**/*.pem"]
