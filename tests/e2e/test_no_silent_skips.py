"""A skipped test reads exactly like a passing one, and that is how three runtimes went untested.

CI installed `.[dev,cli,graph,mcp,console]`. The `sdks` extra was not in that list, so every test
of the Anthropic, OpenAI Agents, LangChain and LangGraph adapters hit `pytest.importorskip` and
vanished into a skip count nobody reads. Eighteen tests covering the three integrations most people
reach for were not running, and the LangGraph defect — a blocked call raising `TypeError` inside
`ToolNode` and killing the whole graph — went out through a green build.

The install line is fixed. This is the part that keeps it fixed: with `NETI_REQUIRE_SDKS=1` set,
every optional import a skip guard depends on must actually import. CI sets it; a fresh clone does
not, so `pip install neti` and `pytest` still works without pulling in four agent frameworks.

Same shape as `test_platform_imports.py` and `test_docs_are_true.py`: an executable invariant over
the whole repo rather than another example.
"""

from __future__ import annotations

import importlib
import os
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

REQUIRED = {
    "anthropic": "the Anthropic tool_runner adapter",
    "agents": "the OpenAI Agents SDK adapter",
    "langchain_core": "the LangChain adapter",
    "langgraph": "the LangGraph ToolNode path — the only thing that caught the ToolMessage defect",
    "httpx": "the MCP gateway and every Graph resolver",
    "fastapi": "the console API",
    "typer": "every CLI command",
}

requires_full_install = pytest.mark.skipif(
    os.environ.get("NETI_REQUIRE_SDKS") != "1",
    reason="set NETI_REQUIRE_SDKS=1 (CI does) to require the full install",
)


@requires_full_install
@pytest.mark.parametrize("module", sorted(REQUIRED))
def test_every_optional_dependency_is_actually_installed(module: str) -> None:
    """Turns a skip back into a failure, one dependency at a time so the message names the gap."""
    try:
        importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - the failure path is the point
        pytest.fail(
            f"`{module}` is missing, so {REQUIRED[module]} is not being tested. "
            "Install `.[dev,cli,graph,mcp,console,sdks,storage,database]`."
        )
        raise AssertionError from exc


@requires_full_install
def test_node_is_available_for_the_real_mcp_server_test() -> None:
    """`tests/e2e/test_real_mcp_server.py` skips without `npx`, which is the same failure shape.

    stdio is the transport essentially every local MCP server uses, and that file is the only place
    the gate is driven against a real one. Silently skipping it would leave the most common
    deployment untested while the summary stayed green.
    """
    assert shutil.which("npx"), (
        "npx is missing, so the real MCP server test is skipping and the stdio transport is only "
        "being exercised against fakes. CI installs Node for this."
    )


@requires_full_install
def test_every_importorskip_in_the_suite_names_a_dependency_that_is_installed() -> None:
    """The invariant the fixed list above cannot express: a *new* skip guard nobody thought to add.

    Read out of the source rather than by running pytest and parsing its skip report. Spawning the
    suite cost 53 seconds and could only say "something skipped"; this takes milliseconds and names
    the package. It also cannot recurse into itself, which the subprocess version could.

    `tests/live/` is exempt because it is opt-in by design — it needs real provider credentials.
    """
    import re

    pattern = re.compile(r"""importorskip\(\s*["']([A-Za-z_][A-Za-z0-9_.]*)["']""")
    missing: list[str] = []
    for path in sorted((REPO / "tests").rglob("test_*.py")):
        if "live" in path.parts:
            continue
        for module in pattern.findall(path.read_text()):
            try:
                importlib.import_module(module)
            except ImportError:
                missing.append(f"{path.relative_to(REPO)} skips without `{module}`")

    assert not missing, (
        "whole test modules will skip, which is invisible in a -q summary:\n  "
        + "\n  ".join(missing)
        + "\nAdd the package to the CI install line and to `REQUIRED` above."
    )


def test_the_ci_workflow_installs_what_the_tests_import() -> None:
    """The docs-are-true mechanism pointed at CI.

    This is the check that would have caught the original hole on the day it opened: the workflow
    is a file, the extras it installs are a string in that file, and nobody was comparing them to
    the dependencies the suite needs. Runs unconditionally — it reads a file and needs nothing
    installed.
    """
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    install_lines = [ln for ln in workflow.splitlines() if "uv pip install" in ln]
    assert install_lines, "no install line found in ci.yml — has the workflow changed shape?"

    # The bench and packaging jobs deliberately differ; the job that runs the suite is the one that
    # has to be complete, and it is the one carrying the sdks extra.
    suite_installs = [ln for ln in install_lines if "sdks" in ln]
    assert suite_installs, (
        "no CI install line includes the `sdks` extra, so the Anthropic, OpenAI Agents, LangChain "
        "and LangGraph adapters are not being tested in CI"
    )
    for extra in ("sdks", "storage", "database", "console"):
        assert any(extra in ln for ln in suite_installs), f"CI never installs the `{extra}` extra"

    assert "NETI_REQUIRE_SDKS" in workflow, (
        "CI installs the extras but does not set NETI_REQUIRE_SDKS, so a future install-line "
        "regression would go back to skipping silently instead of failing"
    )
