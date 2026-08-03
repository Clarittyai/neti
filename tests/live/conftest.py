"""The live tier writes down what it proved, so M11 stops being a claim about file existence.

`neti score` prints `[verified] db.rows — against Postgres 16, in Docker`. What backed that sentence
was `LIVE_VERIFIED`, a hand-written dict, pinned by a property test that checked
`tests/live/test_postgres_live.py` *exists*. A live test that had rotted, or that skipped every
assertion for want of a container, left the claim standing untouched — the same shape as every other
defect this project has found in itself: evidence that is really an assertion about a filename.

So this records outcomes. `just live` now leaves `eval/results/live_verification.json` behind, in
the shape `eval/surveys/mcp_coverage.py` already established for M10: committed evidence, produced
by a tier that needs real providers and never runs in CI, consumed by the card.

**A skipped module is recorded as skipped, never as passed.** That is the whole value: running the
live tier with no Docker must not be able to look like running it with Docker, and the previous
arrangement could not tell the two apart at all.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "eval" / "results" / "live_verification.json"

# Which resolver each module is evidence for. The same mapping `tests/property/
# test_scorecard_is_true.py` uses to check the modules exist — kept here too because this file is
# what produces the evidence, and a property test asserts the two agree.
PROVES: dict[str, tuple[str, ...]] = {
    "test_postgres_live.py": ("db.rows",),
    "test_s3_live.py": ("storage.objects",),
    "test_terraform_live.py": ("terraform.destroy",),
    "test_github_live.py": ("github.repos", "github.files"),
    # Present and, at the time of writing, never run — nobody here has a tenant. That is precisely
    # why it is listed: a module that records `skipped` is the difference between "we have not
    # verified this" and "we have not even written the check".
    "test_entra_live.py": (
        "entra.principals",
        "entra.apps",
        "entra.guests",
        "entra.principals_with_guests",
    ),
}

_OUTCOMES: dict[str, dict[str, int]] = {}


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any) -> Any:
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" and not (report.when == "setup" and report.skipped):
        return
    module = Path(str(item.fspath)).name
    if module not in PROVES:
        return
    tally = _OUTCOMES.setdefault(module, {"passed": 0, "failed": 0, "skipped": 0})
    if report.passed:
        tally["passed"] += 1
    elif report.failed:
        tally["failed"] += 1
    elif report.skipped:
        tally["skipped"] += 1


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """Write what actually happened, including the modules that did not run.

    Absent from the file is a third state and it means "this session never reached that module" —
    distinct from skipped, which means it was reached and declined for want of a provider. The card
    reads both as *not verified*; conflating them here would lose the only signal that says whether
    somebody ran the tier at all.
    """
    if not _OUTCOMES:
        return

    resolvers: dict[str, dict[str, Any]] = {}
    for module, tally in _OUTCOMES.items():
        verified = tally["passed"] > 0 and tally["failed"] == 0 and tally["skipped"] == 0
        for resolver in PROVES[module]:
            resolvers[resolver] = {"module": module, "verified": verified, **tally}

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(
        json.dumps(
            {
                "metric": "M11",
                "platform": f"{platform.system()} {platform.machine()}",
                "resolvers": dict(sorted(resolvers.items())),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
