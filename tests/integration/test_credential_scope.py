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
    config.write_text(LOCAL_ONLY)

    out = run(["inventory", "--config", str(config)], no_credentials)

    assert out.returncode == 0, out.stderr
    assert "missing credentials" not in out.stderr
    assert "fs.paths" in out.stdout


def test_the_gate_still_decides_without_one(tmp_path: Path, no_credentials: dict[str, str]) -> None:
    """Not just that it starts — that it reaches a real verdict on real data."""
    tree = tmp_path / "tree"
    tree.mkdir()
    for i in range(80):
        (tree / f"f{i}.txt").write_text("x")

    config = tmp_path / "neti.yaml"
    config.write_text(LOCAL_ONLY)

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
    config.write_text(NEEDS_ENTRA)

    out = run(["inventory", "--config", str(config)], no_credentials)

    assert out.returncode != 0
    assert "missing credentials" in out.stderr
