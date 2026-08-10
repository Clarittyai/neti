"""Session totals that survive between processes.

`SessionTally` lives on the `Engine`, which is right for a long-running gateway and **structurally
inert for the integration most people use**: `neti hook` is one process per tool call, so every call
built a fresh engine with an empty tally and a declared session budget could never fire.

That made a `SCOPE.md` claim false exactly where it mattered — NC-01 says cumulative effect is
*"mitigated only by declared session budgets"*, and for a Claude Code user it was not mitigated at
all. Cumulative effect is also most of what a coding agent's traffic *is*: over a simulated week,
178 of 320 calls were single-file operations of magnitude 1, which no per-call ceiling can see.
"""

from __future__ import annotations

import json
from pathlib import Path

from neti.config.policy import Policy
from neti.core.budget import SessionTally, Window
from neti.core.types import ProposedCall
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.resolvers.filesystem import FilesystemResolver
from neti.store.sessions import SessionStore

SESSION = Window()
"""The default window. These tests predate windows existing, and every one of them is about the
session bucket, so they say so explicitly rather than leaning on a default."""

NOW = 1_760_000_000.0
"""A fixed clock. A session bucket ignores it entirely; pinning it here means these tests cannot
start depending on the real one by accident."""


def tree(tmp_path: Path, n: int = 40) -> Path:
    for i in range(n):
        (tmp_path / f"f{i}.ts").write_text("x", encoding="utf-8")
    return tmp_path


def policy_with_budget(above: int = 3) -> Policy:
    return Policy.model_validate(
        {
            "version": 1,
            "mode": Mode.ENFORCE,
            "session_budgets": [
                {
                    "tools": frozenset({"Read"}),
                    "unit": "objects",
                    "bands": ({"above": above, "verdict": "block"},),
                    "window": "session",
                }
            ],
            "tools": {"Read": {"gate": {"/file_path": {"resolver": "fs.paths"}}}},
        }
    )


def fresh_engine(tmp_path: Path, records: Path) -> Engine:
    """A new engine per call, which is what `neti hook` really does — one process each."""
    return Engine(
        policy=policy_with_budget(),
        resolvers={"fs.paths": FilesystemResolver(root=tmp_path)},
        sessions=SessionStore(records),
    )


# --------------------------------------------------------------------------- the defect it fixes


def test_a_budget_fires_across_separate_engines(tmp_path: Path) -> None:
    """The whole point. Six single-object reads, one engine each, budget of 3.

    Before this the tally was empty on every call and all six were allowed — a declared budget that
    could not fire, on the integration the product is mostly installed through.
    """
    work = tree(tmp_path)
    records = tmp_path / "out" / "decisions.ndjson"

    verdicts = [
        fresh_engine(work, records)
        .gate(ProposedCall(tool="Read", args={"file_path": str(work / f"f{i}.ts")}, session_id="s"))
        .decision.verdict.name
        for i in range(6)
    ]

    assert verdicts[:3] == ["ALLOW"] * 3
    assert verdicts[3:] == ["BLOCK"] * 3, "the budget never fired across processes"


def test_a_blocked_call_does_not_consume_budget(tmp_path: Path) -> None:
    """Otherwise one refused attempt poisons the rest of the session — the property `SessionTally`
    already had in memory, which has to survive the round trip through disk."""
    work = tree(tmp_path)
    records = tmp_path / "out" / "decisions.ndjson"

    for i in range(6):
        fresh_engine(work, records).gate(
            ProposedCall(tool="Read", args={"file_path": str(work / f"f{i}.ts")}, session_id="s")
        )

    stored = json.loads((records.parent / "sessions" / "s.json").read_text(encoding="utf-8"))
    assert stored["totals"]["objects"] == 3, "blocked calls were still charged to the session"


def test_sessions_are_counted_apart(tmp_path: Path) -> None:
    work = tree(tmp_path)
    records = tmp_path / "out" / "decisions.ndjson"

    for i in range(4):
        fresh_engine(work, records).gate(
            ProposedCall(tool="Read", args={"file_path": str(work / f"f{i}.ts")}, session_id="a")
        )
    other = fresh_engine(work, records).gate(
        ProposedCall(tool="Read", args={"file_path": str(work / "f9.ts")}, session_id="b")
    )
    assert other.decision.verdict.name == "ALLOW"


# --------------------------------------------------------------------------- what it must not do


def test_nothing_is_read_or_written_without_a_declared_budget(tmp_path: Path) -> None:
    """This runs on the hot path of every tool call. A policy with no budget must not pay for one —
    `neti hook` measures p50 137ms and most of it is interpreter start."""
    work = tree(tmp_path)
    records = tmp_path / "out" / "decisions.ndjson"
    policy = Policy.model_validate(
        {
            "version": 1,
            "mode": Mode.ENFORCE,
            "tools": {"Read": {"gate": {"/file_path": {"resolver": "fs.paths"}}}},
        }
    )
    engine = Engine(
        policy=policy,
        resolvers={"fs.paths": FilesystemResolver(root=work)},
        sessions=SessionStore(records),
    )
    engine.gate(ProposedCall(tool="Read", args={"file_path": str(work / "f1.ts")}, session_id="s"))

    assert not (records.parent / "sessions").exists()


def test_an_unreadable_store_does_not_break_the_gate(tmp_path: Path) -> None:
    """A gate that stopped working because a cache file was unreadable would trade a real guarantee
    for a bookkeeping one. Same rule the record itself follows: the decision survives its filing."""
    work = tree(tmp_path)
    records = tmp_path / "out" / "decisions.ndjson"
    store = SessionStore(records)
    store.root.mkdir(parents=True)
    (store.root / "s.json").write_text("{ not json", encoding="utf-8")

    assert store.load(SESSION, "s", NOW) == SessionTally()
    result = Engine(
        policy=policy_with_budget(),
        resolvers={"fs.paths": FilesystemResolver(root=work)},
        sessions=store,
    ).gate(ProposedCall(tool="Read", args={"file_path": str(work / "f1.ts")}, session_id="s"))
    assert result.decision.verdict.name == "ALLOW"


def test_a_session_id_cannot_escape_the_directory(tmp_path: Path) -> None:
    """A session id is agent-supplied, and this writes to a path derived from it. `../../etc/passwd`
    is a legal string and an illegal filename."""
    from neti.core.types import ArgDecision, Resolution
    from neti.core.units import Unit
    from neti.core.verdict import Verdict

    store = SessionStore(tmp_path / "out" / "decisions.ndjson")
    store.add(
        SESSION,
        "../../../etc/passwd",
        NOW,
        (
            ArgDecision(
                pointer="/p",
                target="a",
                verdict=Verdict.ALLOW,
                resolution=Resolution.resolved(Unit.OBJECTS, 1),
                rule="r",
            ),
        ),
    )

    written = list(store.root.glob("*.json"))
    assert len(written) == 1

    # The property is *where it landed*, not what the name looks like. The sanitised form is
    # `.._.._.._etc_passwd.json`, which still contains dots and cannot traverse anything — asserting
    # on the characters was the first version and was testing the wrong thing.
    assert written[0].resolve().parent == store.root.resolve()
    assert store.root.resolve() in written[0].resolve().parents


def test_parallel_calls_do_not_lose_increments(tmp_path: Path) -> None:
    """Locking only the write does not stop a lost update.

    The comment claiming it did was wrong, which is why this exists.

    The shape was `load()` … decide … `save()`, with the lock around `save` alone: two processes
    both read 3, both write 4, one increment gone. Driven with 24 hook processes at once against a
    single session, **7 of 24 calls were counted** — a 71% loss on the one mechanism `SCOPE.md`
    names as the mitigation for cumulative effect, in exactly the conditions that make cumulative
    effect worth watching. A harness that batches tool calls is ordinary.

    Threads rather than processes here: the lock is `fcntl`/`msvcrt` on a file descriptor, so the
    contention is real either way, and this stays fast enough to run on every commit.
    """
    from concurrent.futures import ThreadPoolExecutor

    from neti.core.types import ArgDecision, Resolution
    from neti.core.units import Unit
    from neti.core.verdict import Verdict
    from neti.store.sessions import SessionStore

    store = SessionStore(tmp_path / "decisions.ndjson")
    one = (
        ArgDecision(
            pointer="/file_path",
            target="a.ts",
            verdict=Verdict.ALLOW,
            resolution=Resolution.resolved(Unit.OBJECTS, 1),
            rule="under_all_bands",
        ),
    )

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda _: store.add(SESSION, "race", NOW, one), range(64)))

    final = store.load(SESSION, "race", NOW)
    assert final.calls == 64, f"{64 - final.calls} increments were lost"
    assert final.totals["objects"] == 64


def test_a_bookkeeping_failure_never_becomes_a_gate_failure(tmp_path: Path) -> None:
    """A directory that cannot be written is a budget that under-counts.

    It is not a tool call that dies."""
    from neti.core.types import ArgDecision, Resolution
    from neti.core.units import Unit
    from neti.core.verdict import Verdict
    from neti.store.sessions import SessionStore

    store = SessionStore(tmp_path / "nope" / "decisions.ndjson")
    (tmp_path / "nope").mkdir()
    (tmp_path / "nope" / "sessions").write_text("not a directory", encoding="utf-8")

    one = (
        ArgDecision(
            pointer="/p",
            target="a",
            verdict=Verdict.ALLOW,
            resolution=Resolution.resolved(Unit.OBJECTS, 1),
            rule="r",
        ),
    )
    assert store.add(SESSION, "s", NOW, one).calls == 1, "the caller still gets a usable tally"
