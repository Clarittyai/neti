"""Budget totals pooled across every machine in the organisation.

`SessionStore` made a budget survive a restart and windows made one span a day, but both are still
*this machine*. A declared "20,000 objects a day" is twenty thousand **per laptop**, so an org
running forty agents declared a limit it does not have. That is the last shape of `SCOPE.md` NC-01
a single machine structurally cannot see, and the reason `LICENSING.md` lists shared budgets as
paid: the rule there is *can one machine do this?*, and one machine cannot know what the other
thirty-nine did.

**These tests are the protocol.** `LICENSING.md` promises the client is open, the wire is readable,
and anybody can write their own server and hold it to the same properties. That promise is only
worth something if the properties are written down as executable assertions rather than as prose, so
every one of them is below — with a fake server, because the point is the contract and not anyone's
implementation of it.

The two that matter most are about *not* becoming a new way to fail:

1. **An outage degrades to the free tier and never blocks more.** A control plane that can stop work
   by being unreachable is exactly what `LICENSING.md` says paying for this does not buy.
2. **The local write always happens**, so an outage starts from what this machine has really done
   rather than from zero.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from neti.approvals import ApproverError
from neti.cloud import SharedTallies
from neti.core.budget import SessionTally, Window
from neti.core.types import ArgDecision, Resolution
from neti.core.units import Unit
from neti.core.verdict import Verdict
from neti.store.sessions import SessionStore

NOW = 1_760_007_600.0


def one(magnitude: int = 1) -> tuple[ArgDecision, ...]:
    return (
        ArgDecision(
            pointer="/file_path",
            target="a.ts",
            verdict=Verdict.ALLOW,
            resolution=Resolution.resolved(Unit.OBJECTS, magnitude),
            rule="under_all_bands",
        ),
    )


class FakeServer:
    """A control plane that keeps totals in a dict. Enough to hold the client to its contract."""

    def __init__(self) -> None:
        self.buckets: dict[str, dict[str, int]] = {}
        self.calls: dict[str, int] = {}
        self.down = False
        self.seen: list[tuple[str, str]] = []
        """Every (method, bucket) the client asked for, so a test can assert what was *not* sent."""

    def _check(self) -> None:
        if self.down:
            raise ApproverError("control plane unreachable: connection refused")

    def totals(self, bucket: str) -> dict[str, Any]:
        self.seen.append(("GET", bucket))
        self._check()
        return {"totals": dict(self.buckets.get(bucket, {})), "calls": self.calls.get(bucket, 0)}

    def add_totals(self, bucket: str, contribution: dict[str, int]) -> dict[str, Any]:
        self.seen.append(("POST", bucket))
        self._check()
        held = self.buckets.setdefault(bucket, {})
        for unit, value in contribution.items():
            held[unit] = held.get(unit, 0) + value
        self.calls[bucket] = self.calls.get(bucket, 0) + 1
        return {"totals": dict(held), "calls": self.calls[bucket]}


@pytest.fixture
def shared(tmp_path: Path) -> tuple[SharedTallies, FakeServer, SessionStore]:
    server = FakeServer()
    local = SessionStore(tmp_path / "out" / "decisions.ndjson")
    return SharedTallies(local=local, client=server), server, local  # type: ignore[arg-type]


DAY = Window.parse("day")


# --------------------------------------------------------------- the hole it closes


def test_two_machines_share_one_daily_total(tmp_path: Path) -> None:
    """The whole point. Twenty thousand a day has to mean twenty thousand, not twenty per laptop."""
    server = FakeServer()
    machines = [
        SharedTallies(
            local=SessionStore(tmp_path / f"m{i}" / "decisions.ndjson"),
            client=server,  # type: ignore[arg-type]
        )
        for i in range(3)
    ]

    for machine in machines:
        machine.add(DAY, "session-on-that-machine", NOW, one(100))

    assert machines[0].load(DAY, "s", NOW).total(Unit.OBJECTS) == 300, (
        "each machine sees what the fleet did, not only what it did"
    )


def test_a_session_window_is_never_pooled(
    shared: tuple[SharedTallies, FakeServer, SessionStore],
) -> None:
    """A session is one conversation on one machine.

    Pooling it would add up unrelated conversations that merely share an id, which is not a total
    anybody declared — and it would send a conversation identifier to the control plane for no
    reason at all.
    """
    tallies, server, _ = shared

    tallies.add(Window(), "s", NOW, one(5))
    assert tallies.load(Window(), "s", NOW).total(Unit.OBJECTS) == 5
    assert server.seen == [], "nothing about a session window should reach the network"


# --------------------------------------------------------------- it must not become a way to fail


def test_an_outage_falls_back_to_this_machine_and_never_raises(
    shared: tuple[SharedTallies, FakeServer, SessionStore],
) -> None:
    """`LICENSING.md`: enforcement takes on no new availability risk by paying us."""
    tallies, server, _ = shared

    tallies.add(DAY, "s", NOW, one(10))
    server.down = True

    assert tallies.add(DAY, "s", NOW, one(7)) is not None
    assert tallies.load(DAY, "s", NOW).total(Unit.OBJECTS) == 17, (
        "the fallback is this machine's own total, which is a floor rather than a guess"
    )


def test_an_outage_under_counts_rather_than_over_blocks(tmp_path: Path) -> None:
    """The direction of the failure, asserted rather than assumed.

    Two machines, then the plane goes away. Each falls back to its own total, which is *lower* than
    the fleet total — so a budget is missed, never wrongly fired. A control plane that could stop
    work by being unreachable is the one thing paying for this must not buy.
    """
    server = FakeServer()
    a = SharedTallies(local=SessionStore(tmp_path / "a" / "d.ndjson"), client=server)  # type: ignore[arg-type]
    b = SharedTallies(local=SessionStore(tmp_path / "b" / "d.ndjson"), client=server)  # type: ignore[arg-type]

    a.add(DAY, "s", NOW, one(100))
    b.add(DAY, "s", NOW, one(100))
    assert a.load(DAY, "s", NOW).total(Unit.OBJECTS) == 200

    server.down = True
    assert a.load(DAY, "s", NOW).total(Unit.OBJECTS) == 100, (
        "the fallback is lower than the fleet total, never higher"
    )


def test_the_local_write_happens_before_the_remote_one(
    shared: tuple[SharedTallies, FakeServer, SessionStore],
) -> None:
    """Otherwise an outage starts the fallback from zero.

    Posting remotely and skipping the local record would mean the first minute of every outage
    forgot everything the machine had already done — which is the failure `SessionStore` exists to
    prevent, reintroduced one layer up.
    """
    tallies, _, local = shared

    tallies.add(DAY, "s", NOW, one(42))
    assert local.load(DAY, "s", NOW).total(Unit.OBJECTS) == 42, (
        "the local floor is written even while the control plane is answering"
    )


def test_a_rejected_key_degrades_rather_than_stopping_the_call(
    shared: tuple[SharedTallies, FakeServer, SessionStore],
) -> None:
    """An expired subscription is an accuracy problem, not an enforcement one."""
    tallies, server, _ = shared

    def refuse(*_: Any, **__: Any) -> dict[str, Any]:
        raise ApproverError("control plane rejected the organisation key")

    server.totals = refuse  # type: ignore[method-assign]
    server.add_totals = refuse  # type: ignore[method-assign]

    assert tallies.add(DAY, "s", NOW, one(3)) is not None
    assert tallies.load(DAY, "s", NOW).total(Unit.OBJECTS) == 3


# --------------------------------------------------------------- the wire


def test_only_magnitudes_and_a_bucket_key_leave_the_machine(
    shared: tuple[SharedTallies, FakeServer, SessionStore],
) -> None:
    """A bucket key is a date, not a conversation, and a contribution is `{unit: integer}`.

    No path, no argument, no session id. What the control plane needs to add up a fleet total is a
    number and somewhere to put it, and sending more than that would make a budget feature into a
    data-sharing decision nobody asked for.
    """
    tallies, server, _ = shared

    tallies.add(DAY, "a-conversation-id", NOW, one(9))

    method, bucket = server.seen[0]
    assert method == "POST"
    assert bucket == "day-2025-10-09"
    assert "a-conversation-id" not in bucket
    assert server.buckets[bucket] == {"objects": 9}


@pytest.mark.parametrize("status", ["404", "405", "503"])
def test_a_server_that_cannot_answer_never_reads_as_an_empty_fleet(status: str) -> None:
    """**A 404 must not mean "empty bucket", and getting this backwards is the dangerous option.**

    It was written the other way first, reasoning that the first call of a new day asks for a bucket
    nobody has written yet. True — and it makes a server that does not implement this route at all
    indistinguishable from one that does. `neti-cloud` today serves approvals and health and
    nothing else, so every `GET` would have returned "empty", every fleet total would have read
    **zero**, and a declared budget would have been compared against a number lower than what this
    one machine had already done. Not merely under-counting: under-counting by everything,
    permanently, with the feature looking switched on.

    Raising sends `SharedTallies` to the local store, which is a floor rather than a guess.
    """
    from neti.cloud import OrgClient

    client = OrgClient.__new__(OrgClient)

    def unavailable(*_: Any, **__: Any) -> dict[str, Any]:
        raise ApproverError(f"control plane returned {status}")

    client._json = unavailable  # type: ignore[method-assign]
    with pytest.raises(ApproverError):
        client.totals("day-2025-10-09")


def test_a_control_plane_without_shared_budgets_says_so_once(
    shared: tuple[SharedTallies, FakeServer, SessionStore],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Silence here is dead config that reads as configured.

    An operator who passed `--org` believes their twenty-thousand-a-day is twenty thousand across
    the fleet. If the control plane cannot answer it is twenty thousand *per machine*, and nothing
    anywhere said so. Once per process, not per call — `neti hook` is a fresh process each time, and
    a line of stderr per tool call is the noise that gets a gate switched off.
    """
    tallies, server, _ = shared
    server.down = True

    tallies.add(DAY, "s", NOW, one(5))
    tallies.add(DAY, "s", NOW, one(5))
    tallies.load(DAY, "s", NOW)

    said = capsys.readouterr().err
    assert said.count("shared budgets unavailable") == 1
    assert "not being enforced across the fleet" in said


# --------------------------------------------------------------- the other axes are untouched


def test_taints_stay_on_the_machine_that_read_the_file(
    shared: tuple[SharedTallies, FakeServer, SessionStore],
) -> None:
    """Sharing a taint would mean an agent that read a support ticket here is downstream of it in
    an unrelated conversation on somebody else's laptop, which is not what provenance claims."""
    from neti.core.provenance import Taint

    tallies, server, local = shared
    tallies.remember_taint("s", Taint(pattern="**/tickets/**", target="t.md", tool="Read"))

    assert tallies.load_taint("s") is not None
    assert local.load_taint("s") is not None
    assert server.seen == [], "a taint is not org state and must not be sent anywhere"


def test_it_satisfies_the_same_interface_as_the_local_store(tmp_path: Path) -> None:
    """The engine duck-types `sessions`, so a divergence here is a gate whose behaviour depends on
    whether somebody is logged in."""
    local = SessionStore(tmp_path / "d.ndjson")
    shared = SharedTallies(local=local, client=FakeServer())  # type: ignore[arg-type]

    for name in ("load", "add", "load_taint", "remember_taint", "sweep"):
        assert callable(getattr(shared, name)), name
        assert callable(getattr(local, name)), name


def test_an_empty_contribution_is_still_a_call(
    shared: tuple[SharedTallies, FakeServer, SessionStore],
) -> None:
    """An unresolved magnitude contributes nothing to the total — the same rule `check_budgets`
    follows, because inventing a contribution would make the total fiction.

    The call is still reported, with an empty contribution. A budget counts magnitudes, but the
    organisation's own view of how busy an agent is should not quietly omit every call the gate
    could not size — that is the population `on_unresolved` exists for.
    """
    tallies, server, _ = shared

    unresolved = (
        ArgDecision(
            pointer="/p",
            target="x",
            verdict=Verdict.ALLOW,
            resolution=Resolution.unresolved(Unit.OBJECTS, reason="nothing to size"),
            rule="on_unresolved:unresolved",
        ),
    )
    result = tallies.add(DAY, "s", NOW, unresolved)

    assert isinstance(result, SessionTally)
    assert result.total(Unit.OBJECTS) == 0
    assert server.calls["day-2025-10-09"] == 1, "the call was reported"
    assert server.buckets["day-2025-10-09"] == {}, "and contributed nothing to any total"
