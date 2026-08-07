"""Suite-wide guards.

One so far, and it earns the file: the test suite must not raise real OS notifications on the
machine running it. `tests/e2e/test_seam_equivalence.py` drives a real flagged call through the real
Claude Code hook, which is exactly right — and it means `pytest` popped a notification on somebody's
desktop, twice, per run.

Set here rather than inside the notifier, because "am I under test" is not a question production
code should ask itself. `tests/integration/test_notify.py` unsets it deliberately for the handful of
cases that are about the notifier.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_desktop_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETI_NO_NOTIFY", "1")
