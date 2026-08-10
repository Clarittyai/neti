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


def test_every_known_rule_matches_its_own_evidence() -> None:
    """A rule is only ever proposed because something real was found. It has to match that thing.

    **It did not, for three of four SSH key types.** One entry carried the glob `**/id_rsa*` and the
    names `id_rsa`, `id_ed25519`, `id_ecdsa`, `id_dsa` — so finding an `id_ed25519` outside `.ssh/`
    proposed a rule that cannot match `id_ed25519`. Dead config, offered *because* of a file it
    could never fire on, by the one module whose entire discipline is "real, or absent".

    Asserted over the whole table rather than fixed in place, because the next entry somebody adds
    is where this comes back.
    """
    from neti.core.globs import matches
    from neti.insight.secrets_scan import KNOWN

    for rule in KNOWN:
        for name in rule.names:
            target = f"/project/{name}"
            assert matches(target, (rule.match,)) is not None, (
                f"{rule.match!r} is proposed when {name!r} is found, and does not match it"
            )
        for directory in rule.dirs:
            target = f"/project/{directory}/something"
            assert matches(target, (rule.match,)) is not None, (
                f"{rule.match!r} is proposed when {directory}/ is found, and does not match it"
            )


def test_every_known_rule_declares_a_usable_verdict() -> None:
    """The fragment is pasted into a policy, so `verdict:` has to be one the policy accepts."""
    from neti.core.verdict import Verdict
    from neti.insight.secrets_scan import KNOWN

    allowed = {v.name.lower() for v in Verdict}
    for rule in KNOWN:
        assert rule.verdict in allowed, f"{rule.match!r} proposes verdict {rule.verdict!r}"
        assert rule.why, f"{rule.match!r} has no reason, and a rule with no reason gets disabled"


def test_the_proposed_fragment_loads_as_a_policy(tmp_path: Path) -> None:
    """Everything this prints is meant to be pasted.

    If it does not parse, the instruction is a lie.
    """
    import yaml

    from neti.config.policy import Policy
    from neti.insight.secrets_scan import render, scan

    (tmp_path / ".env").write_text("K=v", encoding="utf-8")
    (tmp_path / ".npmrc").write_text("//registry:_authToken=x", encoding="utf-8")
    (tmp_path / "server.p12").write_text("x", encoding="utf-8")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_ed25519").write_text("x", encoding="utf-8")

    fragment = render(scan(tmp_path))
    body = "\n".join(line for line in fragment.splitlines() if not line.lstrip().startswith("#"))
    start = body.find("sensitive:")
    assert start >= 0, f"no pasteable block in:\n{fragment}"

    parsed = yaml.safe_load(body[start:])
    policy = Policy.model_validate({"version": 1, **parsed})
    matched = {r.match for r in policy.sensitive}
    assert "**/.env*" in matched and "**/.npmrc" in matched and "**/*.p12" in matched
