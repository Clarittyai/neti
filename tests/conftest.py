"""Suite-wide guards.

Two, and they are the same kind of thing: a test that fails for a reason that is not about the
product.

The first: the test suite must not raise real OS notifications on the machine running it.
`tests/e2e/test_seam_equivalence.py` drives a real flagged call through the real Claude Code hook,
which is exactly right — and it means `pytest` popped a notification on somebody's desktop, twice,
per run. Set here rather than inside the notifier, because "am I under test" is not a question
production code should ask itself. `tests/integration/test_notify.py` unsets it deliberately for the
handful of cases that are about the notifier.

The second: Hypothesis's per-example deadline, which measures the machine.

Fourteen `@given` tests here check determinism, monotonicity, direction soundness and purity. Not
one of them is a claim about how long anything takes — that claim lives in `tests/bench`, which uses
no Hypothesis at all. They inherited the default 200ms per example anyway, and a busy machine turns
that into a failure in a suite where nothing went wrong.

The failure does not even arrive legibly. One slow example out of a hundred is reported as
`FlakyFailure` rather than `DeadlineExceeded`, because Hypothesis retries, the retry is fast, and it
concludes the test is unreliable. Reproduced deterministically: a single `sleep(0.25)` inside
`test_canonicalisation_is_idempotent` produces exactly that, naming a large generated structure and
pointing the reader at the canonicaliser. The canonicaliser is fine.

This repository has already decided this once — `314ad46`, "the last timing tripwire measured the
machine too" — and the reasoning is the same. A wall-clock threshold inside a correctness test is a
tripwire strung across the machine rather than the code.
"""

from __future__ import annotations

import pytest
from hypothesis import settings

settings.register_profile("neti", deadline=None)
settings.load_profile("neti")


@pytest.fixture(autouse=True)
def _no_desktop_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETI_NO_NOTIFY", "1")
