"""The in-process seam.

The important assertions here are not that a big call is blocked — `test_gateway.py` already
establishes that. They are that this seam behaves *identically* to the other two: the same sentence,
the same numbers, the same records. Three transports that disagree about a verdict is three
products, and an agent would be able to tell which one it was talking to.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neti import Blocked, Preflight
from neti.adapters.claude_code import run_hook
from neti.config.policy import Policy, load_policy
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.synthetic import default_tenant
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from neti.store.jsonl import read_records
from tests.integration.test_inventory import EXAMPLE

CRED = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")


@pytest.fixture
def pf() -> Preflight:
    return Preflight.demo(EXAMPLE, mode="enforce")


def test_check_reports_a_call_that_fits(pf: Preflight) -> None:
    verdict = pf.check("send_email", {"to": "g-team"})
    assert verdict.proceeds
    assert verdict.verdict == "allow"
    assert verdict.message == ""


def test_check_reports_the_oversized_call(pf: Preflight) -> None:
    verdict = pf.check("remove_group_members", {"group": "g-eng-all"})
    assert not verdict.proceeds
    assert verdict.verdict == "block"
    assert "41,203" in verdict.message
    assert verdict.payload["resolved"] == 41203


def test_dispatch_runs_the_tool_when_the_call_fits(pf: Preflight) -> None:
    ran: list[str] = []
    out = pf.dispatch("send_email", {"to": "g-team"}, lambda: ran.append("sent") or "sent 25")
    assert out == "sent 25"
    assert ran == ["sent"]


def test_dispatch_returns_the_denial_instead_of_running_the_tool(pf: Preflight) -> None:
    """The denial is a value, not an exception: the caller's next line hands it to the model."""
    ran: list[str] = []

    def never() -> str:
        ran.append("removed")
        return "removed 41,203"

    out = pf.dispatch("remove_group_members", {"group": "g-eng-all"}, never)
    assert ran == []
    assert isinstance(out, str)
    assert "Narrow the target" in out


def test_guard_raises_on_an_oversized_call(pf: Preflight) -> None:
    @pf.guard
    def remove_group_members(group: str) -> str:
        raise AssertionError("a blocked call must never reach the tool")

    with pytest.raises(Blocked) as caught:
        remove_group_members(group="g-eng-all")
    assert caught.value.payload["resolved"] == 41203
    assert "41,203" in caught.value.message


def test_guard_passes_a_call_that_fits_straight_through(pf: Preflight) -> None:
    @pf.guard
    def send_email(to: str) -> str:
        return f"sent to {to}"

    assert send_email(to="g-team") == "sent to g-team"


def test_guard_keeps_the_function_it_wrapped(pf: Preflight) -> None:
    @pf.guard
    def send_email(to: str) -> str:
        """Send a note."""
        return "ok"

    assert send_email.__name__ == "send_email"
    assert send_email.__doc__ == "Send a note."


def test_unsizeable_target_does_not_proceed(pf: Preflight) -> None:
    verdict = pf.check("send_email", {"to": "g-ddg"})
    assert not verdict.proceeds
    assert verdict.verdict == "confirm"
    assert verdict.payload["resolved"] is None


def test_observe_mode_decides_and_forwards_anyway() -> None:
    """Observe must never withhold a call, or the install stops being reversible."""
    pf = Preflight.demo(EXAMPLE, mode="observe")
    verdict = pf.check("remove_group_members", {"group": "g-eng-all"})
    assert verdict.proceeds
    assert verdict.verdict == "block"  # the verdict is reached; only the enforcement differs


def test_the_denial_is_word_for_word_the_hook_denial(pf: Preflight) -> None:
    """One denial, one owner, across all three seams."""
    policy: Policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.ENFORCE})
    client = GraphClient(CRED, transport=default_tenant().transport())
    engine = Engine(policy=policy, resolvers=resolvers_for_client(client))
    hooked = run_hook(
        engine,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "remove_group_members",
            "tool_input": {"group": "g-eng-all"},
        },
    )

    verdict = pf.check("remove_group_members", {"group": "g-eng-all"})
    assert verdict.message == hooked["hookSpecificOutput"]["permissionDecisionReason"]


def test_decisions_land_in_the_same_record_chain(tmp_path: Path) -> None:
    records = tmp_path / "decisions.ndjson"
    pf = Preflight.demo(EXAMPLE, mode="enforce", records=records)
    pf.check("send_email", {"to": "g-team"})
    pf.check("remove_group_members", {"group": "g-eng-all"})
    pf.close()

    stored = list(read_records(str(records)))
    assert [str(r.verdict) for r in stored] == ["allow", "block"]
    assert stored[1].prev_digest == stored[0].record_digest


def test_a_second_process_continues_the_chain(tmp_path: Path) -> None:
    """A restart must not write a break that `neti verify` reports as tampering."""
    from neti.core.record import verify_chain

    records = tmp_path / "decisions.ndjson"
    first = Preflight.demo(EXAMPLE, mode="enforce", records=records)
    first.check("send_email", {"to": "g-team"})
    first.close()

    second = Preflight.demo(EXAMPLE, mode="enforce", records=records)
    second.check("send_email", {"to": "g-team"})
    second.close()

    ok, broken_at = verify_chain(list(read_records(str(records))))
    assert (ok, broken_at) == (True, None)


def test_a_session_id_reaches_the_cumulative_budget() -> None:
    """The one thing this seam can silently get wrong.

    Every one of these calls is 25 recipients and passes the per-call ceiling on its own; only the
    running total crosses the declared session budget. If `session_id` were dropped on the way
    through, each call would tally against its own fresh session, nothing would ever accumulate, and
    the NC-01 mitigation would be dead without a single test failing.
    """
    pf = Preflight.demo(EXAMPLE, mode="enforce")

    stopped_at = None
    for i in range(1, 21):
        verdict = pf.check("send_email", {"to": "g-team"}, session_id="s1")
        if not verdict.proceeds:
            stopped_at = i
            break

    assert stopped_at is not None, "the session budget never fired"
    assert verdict.rule.startswith("session_budget:")
    assert "session" in json.dumps(verdict.payload)

    # A different session starts from zero — the budget is per session, not global.
    assert pf.check("send_email", {"to": "g-team"}, session_id="s2").proceeds


def test_concurrent_processes_do_not_fork_the_chain(tmp_path: Path) -> None:
    """The bug a real agent found and no single-writer test could.

    Claude Code runs tool calls in parallel, and as a `PreToolUse` hook every call is its own
    process. Two of them read the same chain head, both sealed against it, and both appended — one
    `prev_digest` claimed by two records, and `neti verify` correctly reported a broken chain on a
    chain nobody had tampered with.

    The fix is that the sink re-seals under an exclusive file lock, so this is the shape that has to
    hold: N *processes*, one file, no forks. A `threading.Lock` would pass a threads-only version of
    this test and prevent nothing.
    """
    import collections
    import subprocess
    import sys

    records = tmp_path / "concurrent.ndjson"
    script = (
        "import sys;"
        "from neti.preflight import Preflight;"
        f"pf = Preflight.demo({str(EXAMPLE)!r}, mode='enforce', records={str(records)!r});"
        "pf.check('send_email', {'to': 'g-team'});"
        "pf.close()"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2])
        for _ in range(8)
    ]
    for proc in procs:
        assert proc.wait(timeout=120) == 0

    stored = list(read_records(str(records)))
    assert len(stored) == 8

    prevs = collections.Counter(r.prev_digest for r in stored)
    forks = {d: n for d, n in prevs.items() if n > 1}
    assert not forks, f"a prev_digest was claimed by more than one record: {forks}"

    from neti.core.record import verify_chain

    assert verify_chain(stored) == (True, None)
