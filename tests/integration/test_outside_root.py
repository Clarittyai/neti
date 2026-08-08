"""The agent's most valuable secrets are not in the project.

`neti start` puts `.env` off limits by scanning the root. `~/.ssh/id_rsa`, `~/.aws/credentials` and
`~/.config/gh/hosts.yml` are not in the root, so nothing scans them — and each is one object, under
every ceiling anybody would write. Measured on a day-zero install before this existed:

    Read(.env)                 ASK
    Read(~/.ssh/id_rsa)        ALLOWED, silently
    Read(~/.aws/credentials)   ALLOWED, silently
    Read(../../../etc/passwd)  ALLOWED, silently

Three things had to line up. The scan walks the root. `providers.fs.root` bounds what
`reachable_max` reports but not what `resolve` will size. And one file clears every band.

**Location rather than magnitude**, which is why it is allowed to stop a call under the same rule
that lets an off-limits file stop one: it is a fact about where the target is, not a number anybody
chose. And flagging would be useless — an agent that *reads* `~/.aws/credentials` has already put it
in the context window, and the context window goes to the model provider.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from neti.config.policy import Policy
from neti.core.types import ProposedCall
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.resolvers.filesystem import FilesystemResolver
from neti.resolvers.location import outside


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "project"
    (work / "src").mkdir(parents=True)
    (work / "src" / "a.ts").write_text("x", encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "secrets.txt").write_text("k", encoding="utf-8")
    return work


def engine_for(repo: Path, verdict: str | None) -> Engine:
    policy = Policy.model_validate(
        {
            "version": 1,
            "mode": Mode.ENFORCE,
            "providers": {"fs": {"root": str(repo)}},
            "outside_root": verdict,
            # `allow`, so an unresolved target does not mask the verdict under test. A relative
            # path resolves against the *working directory* for `fs.paths` and against the declared
            # root for the location check; under `neti hook` those are the same place, and in a test
            # they are not.
            "tools": {
                "Read": {"gate": {"/file_path": {"resolver": "fs.paths", "on_unresolved": "allow"}}}
            },
        }
    )
    return Engine(policy=policy, resolvers={"fs.paths": FilesystemResolver(root=repo)})


def verdict_of(engine: Engine, target: str) -> str:
    return engine.gate(ProposedCall(tool="Read", args={"file_path": target})).decision.verdict.name


# --------------------------------------------------------------------------- the hole it closes


def test_a_credential_outside_the_project_is_stopped(repo: Path) -> None:
    escape = str(repo.parent / "elsewhere" / "secrets.txt")

    assert verdict_of(engine_for(repo, None), escape) == "ALLOW", (
        "one object, outside the scan, under every ceiling — this was the hole"
    )
    assert verdict_of(engine_for(repo, "confirm"), escape) == "CONFIRM"


def test_a_traversal_is_recognised_after_normalising(repo: Path) -> None:
    """`../../../etc/passwd` is only an escape once the `..` are resolved."""
    assert verdict_of(engine_for(repo, "confirm"), "../elsewhere/secrets.txt") == "CONFIRM"


def test_work_inside_the_project_is_untouched(repo: Path) -> None:
    """The entire cost of the feature, and it has to be nothing."""
    engine = engine_for(repo, "confirm")

    assert verdict_of(engine, str(repo / "src" / "a.ts")) == "ALLOW"
    assert verdict_of(engine, "src/a.ts") == "ALLOW"
    assert verdict_of(engine, str(repo / "src" / "**" / "*.ts")) == "ALLOW"


def test_nothing_declared_changes_nothing(repo: Path) -> None:
    assert verdict_of(engine_for(repo, None), "/etc/hosts") == "ALLOW"


# --------------------------------------------------------------------------- the check itself


def test_the_glob_prefix_is_what_decides(repo: Path) -> None:
    """A pattern is anchored by whatever precedes its first wildcard, and expanding it would cost a
    walk the resolver is about to do anyway."""
    assert outside("src/**/*.ts", repo) is False
    assert outside("/etc/**/*.conf", repo) is True


def test_the_temp_directory_is_not_an_escape() -> None:
    """Scratch files are ordinary agent work and are not somebody's credentials. Confirming every
    one of them is how a control gets switched off.

    The root here is this repository rather than the fixture, and that is the point of the next
    test: the exemption is conditional on the project not being in the temp directory itself.
    """
    here = Path(__file__).resolve().parent.parent.parent
    assert outside(str(Path(tempfile.gettempdir()) / "scratch.txt"), here) is False


def test_the_temp_exemption_does_not_apply_to_a_project_inside_temp(repo: Path) -> None:
    """Otherwise it swallows everything. A checkout under `/tmp` would make every sibling there
    invisible, including one holding somebody's keys.

    Found by running the tests: pytest's `tmp_path` lives under the system temp directory, so every
    escape they could construct was exempt and the check silently passed all of them.
    """
    assert outside(str(repo.parent / "elsewhere" / "secrets.txt"), repo) is True


def test_a_path_that_does_not_exist_yet_is_not_an_escape(repo: Path) -> None:
    """Most of what `Write` does. Treating unresolvable as outside would be the safe direction and
    would fire on every new file."""
    assert outside(str(repo / "src" / "brand-new.ts"), repo) is False


def test_no_root_means_no_inside_to_be_outside_of(repo: Path) -> None:
    assert outside("/etc/hosts", None) is False


def test_the_agent_is_told_to_work_inside_the_project(repo: Path) -> None:
    """ "Narrow the target" is the wrong correction — it would send the agent to read a smaller part
    of somebody's home directory."""
    from neti.gateway.mcp import explain_denial

    engine = engine_for(repo, "confirm")
    result = engine.gate(
        ProposedCall(
            tool="Read", args={"file_path": str(repo.parent / "elsewhere" / "secrets.txt")}
        )
    )
    sentence = explain_denial(result, engine.denial_payload(result))

    assert "outside the directory" in sentence
    assert "Narrow the target" not in sentence
