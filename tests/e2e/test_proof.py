"""`neti prove` is the demo's evidence, so its honesty is the thing under test.

Two failure modes, and only one of them is about verdicts.

The first is the ordinary one: a door disagrees. That is a product defect and the command exits
non-zero for it.

The second is the one that would actually cost a customer's trust — printing a row that *looks*
measured for a door this machine never opened. The four SDK adapters need SDKs the wheel does not
ship, so on most installs some seams cannot be driven, and the difference between "we ran this" and
"a test runs this" has to survive every future edit to the renderer.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from neti.eval import proof as P
from tests.integration.test_inventory import EXAMPLE


@pytest.fixture
def ran(tmp_path: Path) -> P.Proof:
    return P.run_proof(str(EXAMPLE), tmp_path / "proof.ndjson")


def test_every_door_that_opened_gave_the_same_answer(ran: P.Proof) -> None:
    """The claim the command exists to make."""
    assert ran.driven, "no seam was driven at all"
    assert ran.agreed, {s.seam: (s.verdict, s.magnitude, s.sentence[:60]) for s in ran.driven}
    assert all(s.verdict == "block" for s in ran.driven)
    assert all(s.magnitude == 41_203 for s in ran.driven)


def test_the_chain_it_writes_verifies(ran: P.Proof) -> None:
    """The proof is the chain, not the table. Any program can print eleven identical lines."""
    assert ran.chain_ok
    assert ran.records == len(ran.driven), "a driven seam did not seal a record"
    assert ran.head


def test_the_records_say_the_numbers_are_synthetic(ran: P.Proof, tmp_path: Path) -> None:
    """It runs against the fixture tenant, and the evidence has to admit that itself.

    Read back off the file rather than from the in-memory result: the file is what `neti verify`
    reads and what somebody would inspect, and it is the only copy that matters.
    """
    from neti.store.jsonl import read_records

    written = list(read_records(tmp_path / "proof.ndjson"))
    assert written
    assert all(record.synthetic for record in written)


def test_the_seams_it_covers_are_the_seams_the_table_drives() -> None:
    """Anti-drift, the same way the scorecard is pinned.

    `prove` ships in the wheel and the seam-equivalence table does not, so they are necessarily two
    lists. Two lists is how a coverage claim goes stale — a seam added to one and not the other
    would leave the demo either under-selling the product or claiming a door nothing exercises.
    """
    from tests.e2e.test_seam_equivalence import SEAMS

    assert set(P.NEEDS) == set(SEAMS)
    assert set(P.WHAT) == set(SEAMS)
    assert set(P.DRIVERS) == set(SEAMS)


def test_a_missing_sdk_is_never_rendered_as_a_measurement(tmp_path: Path) -> None:
    """The dishonest output this command must not be able to produce.

    With an SDK absent the seam still has to appear — silence would let a reader infer coverage that
    is not there — but it must appear as *not driven*, naming what is missing and what does drive
    it. Simulated by making the availability check say no, which is exactly what a bare
    `pip install neti` looks like.
    """
    with mock.patch.object(P, "_available", lambda module: module is None):
        ran = P.run_proof(str(EXAMPLE), tmp_path / "sparse.ndjson")

    assert ran.cited, "nothing was reported as absent"
    assert all(s.verdict is None and s.magnitude is None for s in ran.cited), (
        "a seam that was never driven carries a verdict or a magnitude"
    )

    rendered = P.format_proof(ran)
    for seam in ran.cited:
        line = next(ln for ln in rendered.splitlines() if ln.strip().startswith(seam.seam))
        assert "not here" in line
        assert "41,203" not in line, f"{seam.seam} was never driven and its row shows a magnitude"
        assert P.PROVEN_BY in line, "an absent seam must name what does prove it"


def test_the_four_seams_that_need_nothing_always_run(ran: P.Proof) -> None:
    """A bare `pip install neti` still opens four doors, and that is the floor the demo rests on.

    If these ever start needing an extra, the demo on a fresh machine degrades to citations only —
    which is a fair report and a much weaker thing to show somebody.
    """
    always = {s.seam for s in ran.driven} & {"preflight", "hook", "mcp-http", "mcp-stdio"}
    assert always == {"preflight", "hook", "mcp-http", "mcp-stdio"}
    assert all(P.NEEDS[seam] is None for seam in always)


def test_it_names_a_verify_command_that_actually_works(ran: P.Proof, tmp_path: Path) -> None:
    """An instruction that does not work is worse than none, and this one is easy to get wrong.

    Observe and enforce are different policies with different digests, so the obvious
    `neti verify -r … --config …` reports every record as "decided under a different policy" — which
    reads as a failure when it is the design working correctly.
    """
    import subprocess
    import sys

    rendered = P.format_proof(ran)
    assert "--mode enforce" in rendered

    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "neti.cli",
            "verify",
            "-r",
            str(tmp_path / "proof.ndjson"),
            "--config",
            str(EXAMPLE),
            "--mode",
            "enforce",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-800:]
    assert "chain intact" in out.stdout
    assert f"{len(ran.driven)} decision(s) replay to the same verdict" in out.stdout
    assert "SYNTHETIC" in out.stdout, "the auditor's command must still report provenance"


def test_every_seam_prove_names_is_a_seam_prove_can_drive() -> None:
    """Three tables describe the doors, and a key in one of them but not the others is a crash.

    Found by adding three adapters and updating two of the three: `neti prove` died with a bare
    `KeyError: 'llamaindex'` under a rich traceback, which reads as a broken product rather than as
    a half-finished registration. The tables are small and the mistake is invisible in review, so
    it is asserted instead.
    """
    from neti.eval.proof import DRIVERS, NEEDS, WHAT

    assert set(NEEDS) == set(DRIVERS) == set(WHAT), (
        "these tables disagree about which seams exist:\n"
        f"  in NEEDS but not DRIVERS: {sorted(set(NEEDS) - set(DRIVERS))}\n"
        f"  in DRIVERS but not NEEDS: {sorted(set(DRIVERS) - set(NEEDS))}\n"
        f"  in NEEDS but not WHAT:    {sorted(set(NEEDS) - set(WHAT))}"
    )
