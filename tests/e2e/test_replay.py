"""Replaying an audit log, and the class of failure only replay can see.

`neti verify` has two jobs and they answer different questions:

- the hash chain answers *"has this record been altered?"*
- replay answers *"does this verdict still follow from this evidence?"*

The second is the one the architecture is arranged around — resolvers do the I/O, `decide` is pure,
and a record keeps the resolutions precisely so the decision can be re-run. It was also the one
nothing exercised, while the command's own docstring claimed it.

The distinction is sharp and this file is built on it: **tampering breaks the chain, and a change to
the decision procedure does not.** A regression in `decide` leaves every digest valid and every
verdict wrong, which is exactly the failure an auditor is relying on this tool to notice.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from neti.config.policy import Policy, load_policy
from neti.core.record import verify_chain
from neti.core.verdict import Mode
from neti.insight.replay import replay
from neti.preflight import Preflight
from neti.store.jsonl import read_records
from tests.integration.test_inventory import EXAMPLE

CORPUS = [
    ("send_email", {"to": "g-team"}),
    ("send_email", {"to": "g-dept"}),
    ("send_email", {"to": "g-eng-all"}),
    ("remove_group_members", {"group": "g-eng-all"}),
    ("remove_group_members", {"group": "not-a-real-group"}),
    ("read_documentation", {"topic": "anything"}),
]


def enforcing() -> Policy:
    """The policy the records were actually made under.

    `mode` is inside the policy digest, and correctly so — observe and enforce are different
    policies and a record says which one judged it. Replaying enforce-mode records against the
    observe-mode file on disk is therefore a *different policy*, reported rather than compared.
    """
    return load_policy(EXAMPLE).model_copy(update={"mode": Mode.ENFORCE})


@pytest.fixture
def recorded(tmp_path: Path) -> Path:
    """A log with one of everything: allow, confirm, block, unsizeable and ungated."""
    records = tmp_path / "decisions.ndjson"
    pf = Preflight.demo(EXAMPLE, mode="enforce", records=records)
    for tool, args in CORPUS:
        pf.check(tool, args)
    return records


def test_every_recorded_verdict_re_derives_from_its_own_evidence(recorded: Path) -> None:
    """The claim the command makes. Every branch of `decide` is represented in the corpus."""
    result = replay(read_records(recorded), enforcing())

    assert result.total == len(CORPUS)
    assert result.replayed == len(CORPUS), "every record was written under this policy"
    assert result.ok, [m.__dict__ for m in result.mismatches]


def test_replay_catches_a_decision_procedure_change_that_the_chain_cannot(
    recorded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point, demonstrated rather than asserted.

    Loosening a ceiling changes what `decide` would answer today while leaving every stored byte
    untouched — so the chain still verifies perfectly and the verdicts are now wrong. Nothing but
    replay can tell you that, and it is precisely the situation an upgrade creates.
    """
    chain = list(read_records(recorded))
    ok, _ = verify_chain(chain)
    assert ok, "the chain is intact and stays intact — that is the premise"

    loosened = enforcing()
    # Raise every ceiling out of reach. The recorded blocks would now be allows.
    for spec in loosened.tools["send_email"].gate.values():
        object.__setattr__(spec, "bands", ())
        object.__setattr__(spec, "breakdown_bands", {})
    for spec in loosened.tools["remove_group_members"].gate.values():
        object.__setattr__(spec, "bands", ())

    result = replay(chain, loosened)

    # The digest changed with the policy, so nothing replays — which is itself the right answer and
    # is reported rather than passed over.
    assert result.other_policy == len(chain)
    assert result.replayed == 0
    assert result.ok, "a different policy is not a mismatch; it is a different question"

    # Now force the comparison the auditor actually wants: same policy digest, different procedure.
    import neti.core.decide as decide_module

    original = decide_module.decide_arg

    def loosened_decide_arg(  # type: ignore[no-untyped-def]
        pointer: str,
        target: str | None,
        ceiling: object,
        resolution: object,
        **facts: object,
    ):
        # `**facts` passed through rather than named: this stands in for a future `decide_arg`,
        # and a double that pins today's parameter list turns any new measured fact into a false
        # failure here — noise in the one test whose subject is a real behavioural change.
        got = original(pointer, target, ceiling, resolution, **facts)  # type: ignore[arg-type]
        from neti.core.types import ArgDecision
        from neti.core.verdict import Verdict

        return ArgDecision(
            pointer=got.pointer,
            target=got.target,
            verdict=Verdict.ALLOW,  # the regression: everything now passes
            resolution=got.resolution,
            rule=got.rule,
        )

    monkeypatch.setattr(decide_module, "decide_arg", loosened_decide_arg)
    regressed = replay(chain, enforcing())

    assert not regressed.ok, (
        "a decision procedure that now allows everything must be caught by replay — "
        "the chain cannot see it, because not one stored byte changed"
    )
    assert {m.replayed for m in regressed.mismatches} == {"allow"}
    still_ok, _ = verify_chain(chain)
    assert still_ok, "and the chain is still perfectly intact, which is the point"


def test_records_from_another_policy_are_reported_not_silently_skipped(recorded: Path) -> None:
    """A replay that quietly covered a third of the log would be worse than refusing to run."""
    from neti.core.verdict import Verdict

    other = enforcing().model_copy(update={"unknown_tool": Verdict.BLOCK})
    result = replay(read_records(recorded), other)

    assert result.other_policy == len(CORPUS)
    assert result.replayed == 0


def test_an_unresolved_record_replays_without_inventing_a_magnitude(recorded: Path) -> None:
    """`Resolution` refuses a magnitude on a non-RESOLVED state, so a reconstruction that tried to
    put one back would raise rather than quietly turn a failed count into a number."""
    chain = list(read_records(recorded))
    unresolved = [
        r for r in chain if any(c.get("magnitude") is None for c in r.causes) and r.causes
    ]
    assert unresolved, "the corpus must contain an unsizeable call for this to mean anything"

    result = replay(unresolved, enforcing())
    assert result.ok and result.replayed == len(unresolved)


# ---------------------------------------------------------------------------- through the CLI


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "neti.cli", *args], capture_output=True, text=True)


@pytest.fixture
def enforcing_file(tmp_path: Path) -> Path:
    """The example, written out in enforce mode, so its digest matches the records."""
    target = tmp_path / "enforcing.yaml"
    target.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace("mode: observe", "mode: enforce", 1),
        encoding="utf-8",
    )
    return target


def test_verify_replays_when_given_a_policy(recorded: Path, enforcing_file: Path) -> None:
    out = run("verify", "--records", str(recorded), "--config", str(enforcing_file))

    assert out.returncode == 0, out.stderr
    assert "chain intact" in out.stdout
    assert "replay to the same verdict" in out.stdout


def test_verify_without_a_policy_still_only_checks_the_chain(recorded: Path) -> None:
    """The default has to stay cheap and credential-free — it is what someone runs on a log file
    they were handed, with no policy and no context."""
    out = run("verify", "--records", str(recorded))

    assert out.returncode == 0, out.stderr
    assert "chain intact" in out.stdout
    assert "replay" in out.stdout, "it should at least say the stronger check exists"


def test_a_tampered_record_fails_before_replay_is_attempted(recorded: Path, tmp_path: Path) -> None:
    """The two checks in order: an altered log is a chain failure, and there is nothing to replay.

    Editing a verdict is the obvious attack, and it is the one the chain exists for.
    """
    lines = recorded.read_text(encoding="utf-8").splitlines()
    # A record that was *not* already an allow — flipping "allow" to "allow" changes nothing, which
    # is how the first draft of this test passed against an untampered file.
    index, record = next(
        (i, json.loads(line))
        for i, line in enumerate(lines)
        if json.loads(line)["verdict"] == "block"
    )
    record["verdict"] = "allow"
    lines[index] = json.dumps(record)
    tampered = tmp_path / "tampered.ndjson"
    tampered.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = run("verify", "--records", str(tampered), "--config", str(EXAMPLE))

    assert out.returncode == 1
    assert "CHAIN BROKEN" in out.stderr
    assert "replay" not in out.stdout.lower(), "a broken chain must not be replayed as if it were"
