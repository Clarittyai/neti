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
    "langchain": "the LangChain conformance row, which builds a real create_agent",
    "llama_index.core": "the LlamaIndex adapter",
    "smolagents": "the smolagents adapter",
    "agents": "the OpenAI Agents SDK adapter",
    "langchain_core": "the LangChain adapter",
    "langgraph": "the LangGraph ToolNode path — the only thing that caught the ToolMessage defect",
    # The four runtimes added after the first three, and the reason they are a separate extra: their
    # closure is ~200 packages, mostly arriving via CrewAI. Listed here anyway, because an extra CI
    # does not install is an extra whose seams do not run, which is the whole subject of this file.
    "crewai": "the CrewAI hook pair — the only adapter whose denial sentence needs two hooks",
    "pydantic_ai": "the Pydantic AI capability hooks",
    "autogen_core": "the AutoGen workbench wrapper — the one runtime with no before-tool callback",
    "google.adk": "the Google ADK plugin callback",
    "httpx": "the MCP gateway and every Graph resolver",
    "fastapi": "the console API",
    "typer": "every CLI command",
    # These two were the same hole one tier down, and it was open for longer. CI installed the
    # `storage` and `database` extras and `test_the_ci_workflow_installs_what_the_tests_import`
    # asserted that it did — but nothing asserted the packages actually *import*, and no offline
    # test needs them: `test_storage_resolver.py` drives a mock lister and
    # `test_database_resolver.py` drives stdlib sqlite. So both drivers could be absent, as they
    # were in a working
    # checkout, with every storage and database test still passing. The extras being listed is not
    # the same claim as the extras being installed.
    "boto3": "the only path `storage.objects` has to a real object store",
    "psycopg": "the Postgres half of `db.rows` — sqlite goes through the stdlib and proves nothing",
}

SPLIT = {
    "semantic_kernel": "the Semantic Kernel adapter",
}
"""Runtimes that **cannot share an environment with the rest**, and are tested in their own.

`semantic-kernel` 1.36 requires `pydantic<2.12`; `openai-agents` requires `>=2.12.2`. No version
satisfies both, and the newer semantic-kernel that would depends on an `azure-ai-agents` pre-release
with no stable version. `pyproject.toml` declares the conflict and `uv` resolves the two separately.

This list is the dangerous part of that split, which is why it is a list rather than a deletion.
Moving a runtime out of `REQUIRED` is exactly the "it skipped and nobody noticed" failure this whole
file exists to prevent — so the entries here are asserted from **both** sides: absent in the main
environment, and present in their own. A runtime that quietly stopped being tested anywhere would
have to fail one of the two.
"""

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
            "Install `.[dev,cli,graph,mcp,console,sdks,sdks-extended,storage,database]`."
        )
        raise AssertionError from exc


@requires_full_install
@pytest.mark.parametrize("module", sorted(SPLIT))
def test_a_split_runtime_is_tested_in_exactly_one_environment(module: str) -> None:
    """Both sides of the split, so a runtime cannot fall out of both and go quiet.

    `NETI_SPLIT_ENV=1` says this is the dedicated environment, and there the module must import —
    that is the job the separate CI runs exist to do. Everywhere else it must be **absent**, which
    is the assertion that keeps the split honest: if a resolver ever quietly satisfies both at once
    again, the main environment stops being the one the lockfile describes and this says so.
    """
    dedicated = os.environ.get("NETI_SPLIT_ENV") == "1"
    try:
        importlib.import_module(module)
        installed = True
    except ImportError:
        installed = False

    if dedicated:
        assert installed, (
            f"`{module}` is missing from its own environment, so {SPLIT[module]} is not being "
            "tested anywhere. Install `.[dev,cli,graph,mcp,console,sdks-semantic-kernel]`."
        )
    else:
        assert not installed, (
            f"`{module}` is installed alongside the other runtimes, which `pyproject.toml` "
            "declares as a conflict. Either it was resolved upstream — move it back into "
            f"REQUIRED if so — or this environment was assembled by hand and {module} is "
            "running outside its own declared constraints, which is how the conformance table came "
            "to claim a version no resolver could produce."
        )


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
def test_the_console_is_built_so_its_routes_are_actually_served() -> None:
    """The same hole as `npx`, one artifact over — and it was open in CI.

    `src/neti/console` is a *shipped* artifact: `pyproject.toml` declares it under
    `[tool.hatch.build] artifacts`, so the wheel carries a UI. It is built by `just console-sync`
    and gitignored, so a plain checkout does not have one — and the CI workflow did a plain
    checkout. Four tests asserting every console route is served skipped on every run, in the
    surface a customer actually looks at.

    The guard above only catches `importorskip`. This one is a `skipif` on a built file, which no
    regex over the source could have found.
    """
    from neti.api.static import console_dir

    assert console_dir() is not None, (
        "no built console, so `tests/integration/test_console_serving.py` is skipping the routes "
        "the wheel ships. Run `just console-sync` (or `cd web && npm ci && npm run build`)."
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
        for module in pattern.findall(path.read_text(encoding="utf-8")):
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
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    install_lines = [ln for ln in workflow.splitlines() if "uv pip install" in ln]
    assert install_lines, "no install line found in ci.yml — has the workflow changed shape?"

    # The bench and packaging jobs deliberately differ; the job that runs the suite is the one that
    # has to be complete, and it is the one carrying the sdks extra.
    suite_installs = [ln for ln in install_lines if "sdks" in ln]
    assert suite_installs, (
        "no CI install line includes the `sdks` extra, so the Anthropic, OpenAI Agents, LangChain "
        "and LangGraph adapters are not being tested in CI"
    )
    for extra in ("sdks", "sdks-extended", "storage", "database", "console"):
        assert any(extra in ln for ln in suite_installs), f"CI never installs the `{extra}` extra"

    assert "npm run build" in workflow or "console-sync" in workflow, (
        "CI never builds the console, so every test asserting a console route is served skips — "
        "and the console is an artifact the wheel ships."
    )

    assert "NETI_REQUIRE_SDKS" in workflow, (
        "CI installs the extras but does not set NETI_REQUIRE_SDKS, so a future install-line "
        "regression would go back to skipping silently instead of failing"
    )
