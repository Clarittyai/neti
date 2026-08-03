"""A policy should only need the credentials it actually uses.

Found by installing the thing: a policy gating a coding agent on `fs.paths` and `db.rows` — no
Entra resolver anywhere in it — refused to start with

    error: missing credentials: NETI_TENANT_ID, NETI_CLIENT_ID, NETI_CLIENT_SECRET.

because every command built the full Entra registry before looking at the policy. Demanding an
Azure app registration from somebody gating file writes is a wall in front of the cheapest install
this product has, and the test suite could not see it: every test either passes `--demo` or
constructs the resolvers directly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LOCAL_ONLY = """\
version: 1
mode: enforce
unknown_tool: allow

tools:
  cleanup_files:
    gate:
      /path:
        resolver: fs.paths
        bands:
          - { above: 50, verdict: block }
        on_unresolved: block
"""

NEEDS_ENTRA = """\
version: 1
mode: enforce
unknown_tool: allow

tools:
  remove_group_members:
    gate:
      /group:
        resolver: entra.principals
        bands:
          - { above: 200, verdict: block }
        on_unresolved: block
"""


@pytest.fixture
def no_credentials() -> dict[str, str]:
    """A subprocess environment with every Entra variable removed."""
    env = dict(os.environ)
    for name in ("NETI_TENANT_ID", "NETI_CLIENT_ID", "NETI_CLIENT_SECRET"):
        env.pop(name, None)
    return env


def run(args: list[str], env: dict[str, str], stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "neti.cli", *args],
        capture_output=True,
        text=True,
        input=stdin,
        env=env,
    )


def test_a_local_only_policy_needs_no_directory_credential(
    tmp_path: Path, no_credentials: dict[str, str]
) -> None:
    config = tmp_path / "neti.yaml"
    config.write_text(LOCAL_ONLY, encoding="utf-8")

    out = run(["inventory", "--config", str(config)], no_credentials)

    assert out.returncode == 0, out.stderr
    assert "missing credentials" not in out.stderr
    assert "fs.paths" in out.stdout


def test_the_gate_still_decides_without_one(tmp_path: Path, no_credentials: dict[str, str]) -> None:
    """Not just that it starts — that it reaches a real verdict on real data."""
    tree = tmp_path / "tree"
    tree.mkdir()
    for i in range(80):
        (tree / f"f{i}.txt").write_text("x", encoding="utf-8")

    config = tmp_path / "neti.yaml"
    config.write_text(LOCAL_ONLY, encoding="utf-8")

    out = run(
        ["hook", "--config", str(config), "--records", str(tmp_path / "d.ndjson")],
        no_credentials,
        stdin=json.dumps({"tool_name": "cleanup_files", "tool_input": {"path": str(tree)}}),
    )

    assert out.returncode == 0, out.stderr
    assert '"permissionDecision": "deny"' in out.stdout
    assert "80 objects" in out.stdout


def test_a_policy_that_does_use_entra_still_says_so_immediately(
    tmp_path: Path, no_credentials: dict[str, str]
) -> None:
    """The other half. Narrowing when the credential is unnecessary must not weaken the loud,
    early failure when it *is* — a credential problem found at startup is a five-minute fix, and
    the same problem found on the hot path is every gated call failing closed at once.
    """
    config = tmp_path / "neti.yaml"
    config.write_text(NEEDS_ENTRA, encoding="utf-8")

    out = run(["inventory", "--config", str(config)], no_credentials)

    assert out.returncode != 0
    assert "missing credentials" in out.stderr


# ---------------------------------------------------------------- the in-process seam
#
# Every test above runs `neti.cli` in a subprocess, and that is precisely why this file did not
# catch the same defect on the fourth door. `Preflight.from_config` is what the README hands you for
# a tool loop you wrote yourself, it builds its resolvers by its own route, and it kept calling
# `build_entra_resolvers` unconditionally long after the CLI stopped.


@pytest.fixture
def unset_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NETI_TENANT_ID", "NETI_CLIENT_ID", "NETI_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)


def test_the_in_process_seam_needs_no_directory_credential_either(
    tmp_path: Path, unset_credentials: None
) -> None:
    """`Preflight.from_config` on a policy with no Entra gate in it.

    Asserted against the *shipped* `examples/coding-agent.yaml` rather than a fixture written here,
    because the claim being made is about the file we tell people to copy: every gate in it is
    `fs.paths`, it needs no credential anywhere, and it could not be loaded this way at all.
    """
    from neti.preflight import Preflight

    pf = Preflight.from_config("examples/coding-agent.yaml", records=None)

    tree = tmp_path / "tree"
    tree.mkdir()
    for i in range(12):
        (tree / f"f{i}.txt").write_text("x", encoding="utf-8")

    # A real verdict on real data, not merely a constructed object.
    assert pf.check("Read", {"file_path": str(tree / "f0.txt")}).payload["resolved"] == 1


def test_the_in_process_seam_still_refuses_when_entra_is_actually_bound(
    tmp_path: Path, unset_credentials: None
) -> None:
    """The other half again, on this door. Loud and early, or not at all."""
    from neti.resolvers.base import ResolverError

    config = tmp_path / "neti.yaml"
    config.write_text(NEEDS_ENTRA, encoding="utf-8")

    from neti.preflight import Preflight

    with pytest.raises(ResolverError, match="missing credentials"):
        Preflight.from_config(str(config), records=None)
