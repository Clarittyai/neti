"""Write down which runtimes were actually driven, and at what version.

The same rule as `tests/live/conftest.py`, for the same reason: a claim about a third-party
framework is only worth the evidence behind it, and "we tested with LangChain" is a sentence that
survives long after the version it was true of.

So this records the version each row ran against. A matrix that says *LangChain 1.3.14* can be
checked; one that says *LangChain* cannot, and quietly becomes a claim about 2.x the day somebody
upgrades. Skipped rows are recorded as skipped and never as passed — running this suite with no
frameworks installed must not be able to look like running it with all of them.
"""

from __future__ import annotations

import json
import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pytest

from tests.conformance.runtimes import RUNTIMES

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "eval" / "results" / "conformance.json"

DISTRIBUTION = {
    "langgraph": "langgraph",
    "langchain": "langchain",
    "openai-agents": "openai-agents",
    "pydantic-ai": "pydantic-ai",
    "google-adk": "google-adk",
    "autogen": "autogen-agentchat",
    "anthropic": "anthropic",
    "crewai": "crewai",
}

_OUTCOMES: dict[str, str] = {}


def _installed(name: str) -> str:
    try:
        return version(DISTRIBUTION.get(name, name))
    except PackageNotFoundError:
        return ""


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any) -> Any:
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" and not (report.when == "setup" and report.skipped):
        return
    name = next((r.name for r in RUNTIMES if f"[{r.name}]" in item.nodeid), None)
    if name is None:
        return
    if report.failed:
        _OUTCOMES[name] = "failed"
    elif report.skipped:
        _OUTCOMES.setdefault(name, "skipped")
    elif report.passed:
        _OUTCOMES.setdefault(name, "passed")


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if not _OUTCOMES:
        return

    rows = {
        runtime.name: {
            "status": _OUTCOMES.get(runtime.name, "not_run"),
            "depth": runtime.depth,
            "what": runtime.what,
            "version": _installed(runtime.name),
        }
        for runtime in RUNTIMES
    }
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(
        json.dumps(
            {
                "metric": "M13",
                "platform": f"{platform.system()} {platform.machine()}",
                "note": (
                    "Each runtime's own agent loop, driven with a scripted fake model and no "
                    "provider. `passed` means the tool body never ran and the sentence the agent "
                    "was shown matched Preflight byte for byte."
                ),
                "runtimes": dict(sorted(rows.items())),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
