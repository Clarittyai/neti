"""A deletion nobody can measure must not pass in the same silence as `npm test`.

`shell.paths` declines far more than it sizes, which is correct — a wrong number is unsound. But
declining produced one `UNRESOLVED` for two entirely different facts:

    npm test                    this is not a filesystem deletion
    cat list.txt | xargs rm     this is a deletion and I cannot tell you how big

With one hook governing both, an operator had to choose which fact to get wrong. `allow` and an
unmeasured deletion passes invisibly — which is worse than leaving `Bash` ungated, because it looks
like coverage. `confirm` and every ordinary command stops for a human, and the gate is uninstalled
by Friday.

So the resolver reports the difference and `on_unsized_risk` decides it separately. The tests below
are in the order the risk actually runs:

1. the three-way split, as a corpus of real command lines;
2. **the false positives**, which are what decides whether this survives a real session;
3. a flag *proceeds*, and says in the record which branch produced it;
4. a policy written before this field exists behaves exactly as it did.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from neti.config.policy import Policy
from neti.core.record import DecisionRecord, verify_chain
from neti.core.types import ProposedCall
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.gatekeeper import Gatekeeper
from neti.resolvers.shell import ShellPathsResolver, destructive_signal
from neti.store.jsonl import JsonlSink

# --------------------------------------------------------------------------- the corpus

SIZED = [
    "rm -rf {tree}",
    "rm {tree}/f0.txt",
    "find {tree} -delete",
    # `.` is the working directory, which the fixture below makes the tree. Both of these are read
    # as a real target list, so they get a real number and never reach the flag.
    "git clean -fdx",
    "git checkout -- .",
]
"""Commands with a readable target. These get a number and the bands decide — the flag never
applies to them, because there is nothing unsized about them."""

FLAGGED = [
    "cat list.txt | xargs rm",
    "rm -rf $BUILD/dist",
    "find . -name '*.log' | xargs rm -f",
    "bash -c 'rm -rf $BUILD'",
    "bash -lc 'find / -name core | xargs rm'",
    "rm -rf $(cat targets.txt)",
    "dd if=/dev/zero of=/dev/disk2",
]
"""Destroys something; the size is not readable from the string. Every one of these was silent
before, under the same `allow` that keeps `npm test` quiet."""

SILENT = [
    "npm test",
    "npm run build",
    "git status",
    "git log --oneline -20",
    "git diff HEAD~1",
    "ls -la",
    "pytest -q",
    "python manage.py migrate",
    "docker ps -a",
    "curl -sS https://example.com/health",
    "cat README.md",
    "grep -rn 'rm' src/",
    'echo "rm -rf /"',
    "make build 2>&1 | tail -5",
]
"""Ordinary session traffic. **This list is the load-bearing one.** A signal that fires on any of
these costs an operator attention on every command they run, and attention spent on noise is the
mechanism by which security tools get switched off.

Two are there specifically because a naive check gets them wrong: `grep -rn 'rm' src/` and
`echo "rm -rf /"` both contain the verb, and neither runs it. The signal looks at command position,
not at the line."""


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """30 files, and the working directory, because a shell command means `.` and `.` means cwd.

    `providers.fs.root` bounds what `reachable_max` reports, not where a relative path points — a
    glob really does resolve against the directory the agent is standing in. So `git clean -fd`
    is only deterministic here if the test stands somewhere known.
    """
    root = tmp_path / "tree"
    root.mkdir()
    for i in range(30):
        (root / f"f{i}.txt").write_text("x", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def policy_for(**gate: Any) -> Policy:
    return Policy.model_validate(
        {
            "version": 1,
            "mode": Mode.ENFORCE,
            "tools": {
                "Bash": {
                    "gate": {
                        "/command": {
                            "resolver": "shell.paths",
                            "bands": [{"above": 10, "verdict": "block"}],
                            "on_unresolved": "allow",
                            **gate,
                        }
                    }
                }
            },
        }
    )


def gate_for(tree: Path, records: Path, **gate: Any) -> Gatekeeper:
    policy = policy_for(**gate)
    return Gatekeeper(
        engine=Engine(policy=policy, resolvers={"shell.paths": ShellPathsResolver(root=str(tree))}),
        sink=JsonlSink(records),
    )


def decide(gate: Gatekeeper, command: str, tree: Path) -> Any:
    return gate.decide(ProposedCall(tool="Bash", args={"command": command.format(tree=tree)}))


# --------------------------------------------------------------------------- 1. the three-way split


@pytest.mark.parametrize("command", SIZED)
def test_a_readable_command_is_sized_and_the_bands_decide(
    command: str, tree: Path, tmp_path: Path
) -> None:
    """The flag is for the absence of a number. Where there is one, nothing changed.

    The claim is not "these block" — `rm f0.txt` is one file and should not. It is that a command
    with a readable target is decided **by its magnitude**, so the rule names a band comparison and
    never a hook. A regression here would mean the flag had started swallowing sizeable commands,
    which would lose the only thing this resolver was built to do.
    """
    gate = gate_for(tree, tmp_path / "d.ndjson", on_unsized_risk="flag")
    record = decide(gate, command, tree).record

    assert record.causes[0]["magnitude"] is not None, f"{command} has a readable target"
    assert "on_uns" not in record.rule, f"{command} was sized; no hook should have decided it"


@pytest.mark.parametrize("command", FLAGGED)
def test_a_deletion_that_cannot_be_sized_is_flagged(
    command: str, tree: Path, tmp_path: Path
) -> None:
    gate = gate_for(tree, tmp_path / "d.ndjson", on_unsized_risk="flag")
    decision = decide(gate, command, tree)

    assert decision.verdict.name == "FLAG", f"{command} destroys something and was not surfaced"
    assert decision.proceeds, "a flag records the call, it does not stop it"


@pytest.mark.parametrize("command", SILENT)
def test_an_ordinary_command_stays_silent(command: str, tree: Path, tmp_path: Path) -> None:
    """The false-positive rate, asserted rather than hoped for.

    Every entry here is something a coding agent runs several times an hour. If this test starts
    failing because the signal was widened, the correct response is almost always to narrow the
    signal, not to delete the row.
    """
    gate = gate_for(tree, tmp_path / "d.ndjson", on_unsized_risk="flag")
    decision = decide(gate, command, tree)

    assert decision.verdict.name == "ALLOW", f"{command} is not a deletion and must not be flagged"


# --------------------------------------------------------------------------- 2. the signal itself


def test_a_redirect_that_creates_a_file_destroys_nothing(tree: Path, tmp_path: Path) -> None:
    """`>` truncates — but only over a file that is already there.

    `pytest -q > out.txt` is the most common redirect in a session and it destroys nothing. Sizing
    is a pure function of the string; this one question is not, and it is worth the filesystem
    lookup because the alternative is flagging every redirect an agent writes.
    """
    gate = gate_for(tree, tmp_path / "d.ndjson", on_unsized_risk="flag")

    assert decide(gate, f"pytest -q > {tree}/new-report.txt", tree).verdict.name == "ALLOW"
    assert decide(gate, f"pytest -q > {tree}/f0.txt", tree).verdict.name == "FLAG"


def test_the_signal_reads_command_position_not_the_line() -> None:
    """A mention of `rm` is not a use of it, and a signal that could not tell them apart would fire
    on every grep for the word."""
    assert destructive_signal("rm -rf x") is not None
    assert destructive_signal("cat list | xargs rm") is not None
    assert destructive_signal("npm run build && rm -rf dist") is not None

    assert destructive_signal("grep -rn 'rm' src/") is None
    assert destructive_signal('echo "rm -rf /"') is None
    assert destructive_signal("npm test") is None


def test_a_wrapper_script_is_invisible_and_that_is_written_down() -> None:
    """`./cleanup.sh` deletes and no textual signal will ever see it.

    Asserted so the limit is a property of the code rather than a paragraph someone may or may not
    have read. `SCOPE.md` NC-12 states it; this is what keeps the statement true.
    """
    assert destructive_signal("./cleanup.sh") is None
    assert destructive_signal("make clean") is None


# ------------------------------------------------------------------------- 3. what the record says


def test_a_flagged_call_proceeds_and_names_the_branch_that_decided(
    tree: Path, tmp_path: Path
) -> None:
    records = tmp_path / "d.ndjson"
    gate = gate_for(tree, records, on_unsized_risk="flag")
    decide(gate, "cat list.txt | xargs rm", tree)
    gate.sink.close()

    record = json.loads(records.read_text(encoding="utf-8").splitlines()[0])

    assert record["verdict"] == "flag"
    assert record["rule"] == "/command:on_unsized_risk:destructive_but_unsizeable"
    cause = record["causes"][0]
    assert cause["state"] == "unresolved" and cause["magnitude"] is None
    # Why there is no number, and that its absence is the dangerous kind. Without these two an
    # auditor reading the file sees "unresolved" and cannot tell this from `npm test`.
    assert cause["destructive"] == "destructive_verb:rm"
    assert cause["reason"] == "compound_command"


def test_a_flag_replays_as_a_flag(tree: Path, tmp_path: Path) -> None:
    """`neti verify --config` re-derives every verdict from the stored record. A flag has to
    survive that round trip.

    It did not. `decide` routes on a signal that lives in `Resolution.evidence`, and the replay
    rebuilt an unresolved resolution with an empty one — so every recorded `flag` re-derived as
    `allow` and `neti verify` reported the log as inconsistent with itself. The record had carried
    the fact all along; nothing read it back.

    Found by running `neti verify --config` over a real session, not by reading the replay code.
    """
    from neti.insight.replay import replay

    records = tmp_path / "d.ndjson"
    gate = gate_for(tree, records, on_unsized_risk="flag")
    for command in ("npm test", "cat list.txt | xargs rm", "rm -rf {tree}"):
        decide(gate, command, tree)
    gate.sink.close()

    rows = [
        DecisionRecord.model_validate(json.loads(line))
        for line in records.read_text(encoding="utf-8").splitlines()
    ]
    result = replay(rows, policy_for(on_unsized_risk="flag"))

    assert result.replayed == 3, "all three were written under this policy"
    assert result.ok, f"replayed differently: {result.mismatches}"


def test_the_chain_survives_the_new_verdict(tree: Path, tmp_path: Path) -> None:
    records = tmp_path / "d.ndjson"
    gate = gate_for(tree, records, on_unsized_risk="flag")
    for command in ("npm test", "cat list.txt | xargs rm", "rm -rf {tree}"):
        decide(gate, command, tree)
    gate.sink.close()

    rows = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    assert [r["verdict"] for r in rows] == ["allow", "flag", "block"]

    ok, bad = verify_chain([DecisionRecord.model_validate(r) for r in rows])
    assert ok, f"chain broken at {bad}"


# --------------------------------------------------------------------------- 4. nothing else moved


@pytest.mark.parametrize("command", [*FLAGGED, *SILENT])
def test_a_policy_without_the_field_behaves_exactly_as_it_did(
    command: str, tree: Path, tmp_path: Path
) -> None:
    """The field is additive and unset means unchanged.

    Every policy written before this existed keeps its single `on_unresolved` answer for both facts
    — which is the wrong trade, and is also nobody's to change from underneath them.
    """
    gate = gate_for(tree, tmp_path / "d.ndjson")
    assert decide(gate, command, tree).verdict.name == "ALLOW"


def test_the_stricter_posture_is_one_word(tree: Path, tmp_path: Path) -> None:
    """`flag` is the shipped default because a `confirm` with no control plane stops the call, and
    `git rm` is ordinary. An operator who wants it stopped changes one word, and gets an identical
    record with a different consequence."""
    gate = gate_for(tree, tmp_path / "d.ndjson", on_unsized_risk="confirm")
    decision = decide(gate, "cat list.txt | xargs rm", tree)

    assert decision.verdict.name == "CONFIRM"
    assert not decision.proceeds, "and with no approver reachable, it does not run"
    assert "on_unsized_risk" in decision.record.rule
