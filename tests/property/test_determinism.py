"""Invariants 5 and 6: the record is byte-identical across processes, and sensitive to its inputs.

`run(x) == run(x)` inside one process is trivially true and catches nothing. So the real assertion
is byte-equality of the canonical record across *fresh interpreters with different PYTHONHASHSEED
values*, which is what actually shakes out set/dict iteration order leaking into output.

Invariant 6 exists because invariant 5 also passes for a constant function. A determinism suite
without a mutation test proves nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from neti.core.canonical import CanonicalError, canonical_bytes, canonical_json
from neti.core.decide import decide
from neti.core.record import build_record, chain_digest, verify_chain
from neti.core.types import Band, Ceiling, ProposedCall, Resolution
from neti.core.units import Direction, Unit
from neti.core.verdict import Verdict

REPO = Path(__file__).resolve().parents[2]

# A fixed scenario a subprocess can rebuild from scratch. Deliberately includes a breakdown dict and
# multiple gated args, since dict and set ordering is exactly what we are trying to catch.
SCENARIO = """
import json
from neti.core.decide import decide
from neti.core.record import build_record
from neti.core.types import Band, Ceiling, ProposedCall, Resolution
from neti.core.units import Unit
from neti.core.verdict import Verdict

ceiling = Ceiling(
    unit=Unit.PRINCIPALS,
    bands=(Band(above=200, verdict=Verdict.BLOCK), Band(above=25, verdict=Verdict.CONFIRM)),
    breakdown_bands={"guest": (Band(above=100, verdict=Verdict.BLOCK),),
                     "internal": (Band(above=9000, verdict=Verdict.FLAG),)},
)
apps = Ceiling(unit=Unit.APPS, bands=(Band(above=5, verdict=Verdict.BLOCK),))
call = ProposedCall(tool="remove_group_members",
                    args={"group": "engineering-all", "notify": True})
resolutions = {
    "/group": Resolution.resolved(
        Unit.PRINCIPALS, 412, breakdown={"internal": 3100, "guest": 412},
        provider_snapshot="etag-1", consistency="eventual"),
    "/group#apps": Resolution.resolved(Unit.APPS, 9),
}
gated = (("/group", "engineering-all", ceiling), ("/group#apps", "engineering-all", apps))
d = decide(call, gated, resolutions)
rec = build_record(d, decision_id="d-1", decided_at="2026-07-29T09:04:11.204Z",
                   policy_digest="pol-1", code_version="0.1.0",
                   args=call.args, session_id="s-1")
print(json.dumps({"digest": rec.record_digest, "verdict": rec.verdict, "rule": rec.rule}))
"""


def _run_scenario(hashseed: str) -> dict[str, str]:
    env = dict(os.environ, PYTHONHASHSEED=hashseed, PYTHONPATH=str(REPO / "src"))
    out = subprocess.run(
        [sys.executable, "-c", SCENARIO], capture_output=True, text=True, check=True, env=env
    )
    return json.loads(out.stdout)


def test_record_is_byte_identical_across_processes_and_hash_seeds() -> None:
    results = [_run_scenario(seed) for seed in ("0", "1", "42", "random")]
    digests = {r["digest"] for r in results}
    assert len(digests) == 1, f"record digest varied across hash seeds: {results}"
    assert results[0]["verdict"] == "block"


def test_mutating_any_input_changes_the_digest() -> None:
    """Without this, the test above would pass for a function that returns a constant."""
    baseline = _run_scenario("0")["digest"]

    mutations = {
        "magnitude": ("Unit.PRINCIPALS, 412", "Unit.PRINCIPALS, 413"),
        "ceiling": ("above=200", "above=201"),
        "breakdown": ('"guest": 412', '"guest": 413'),
        "policy_digest": ('policy_digest="pol-1"', 'policy_digest="pol-2"'),
        "snapshot": ('provider_snapshot="etag-1"', 'provider_snapshot="etag-2"'),
        "args": ('"notify": True', '"notify": False'),
        "decided_at": ("2026-07-29T09:04:11.204Z", "2026-07-29T09:04:11.205Z"),
    }
    for name, (old, new) in mutations.items():
        assert old in SCENARIO, f"mutation {name!r} no longer applies to the scenario"
        env = dict(os.environ, PYTHONHASHSEED="0", PYTHONPATH=str(REPO / "src"))
        out = subprocess.run(
            [sys.executable, "-c", SCENARIO.replace(old, new)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        digest = json.loads(out.stdout)["digest"]
        assert digest != baseline, f"mutating {name} did not change the record digest"


# --------------------------------------------------------------- canonical json


def test_floats_are_rejected_from_chained_fields() -> None:
    for bad in (1.5, float("nan"), float("inf"), -0.0):
        with pytest.raises(CanonicalError):
            canonical_json({"x": bad})


def test_key_order_is_independent_of_insertion_order() -> None:
    a = {"b": 1, "a": 2, "é": 3, "z": 4}
    b = {"z": 4, "é": 3, "a": 2, "b": 1}
    assert canonical_json(a) == canonical_json(b)


def test_strings_are_nfc_normalised() -> None:
    # "é" as a single code point vs e + combining acute
    assert canonical_json({"k": "é"}) == canonical_json({"k": "é"})


@given(
    st.recursive(
        st.none() | st.booleans() | st.integers(-10**9, 10**9) | st.text(max_size=20),
        lambda children: st.lists(children, max_size=4)
        | st.dictionaries(st.text(max_size=8), children, max_size=4),
        max_leaves=12,
    )
)
def test_canonicalisation_is_idempotent(value: object) -> None:
    once = canonical_json(value)
    assert canonical_json(json.loads(once)) == once


# --------------------------------------------------------------- hash chain


def _record(n: int, prev: str | None) -> object:
    ceiling = Ceiling(unit=Unit.PRINCIPALS, bands=(Band(above=10, verdict=Verdict.BLOCK),))
    d = decide(
        ProposedCall(tool="t"),
        (("/g", "x", ceiling),),
        {"/g": Resolution.resolved(Unit.PRINCIPALS, n)},
    )
    return build_record(
        d,
        decision_id=f"d-{n}",
        decided_at="2026-07-29T00:00:00Z",
        policy_digest="pol",
        code_version="0.1.0",
        prev_digest=prev,
    )


def test_chain_verifies_and_detects_tampering() -> None:
    records = []
    prev = None
    for n in (1, 20, 300):
        rec = _record(n, prev)
        records.append(rec)
        prev = rec.record_digest  # type: ignore[attr-defined]

    ok, bad = verify_chain(records)  # type: ignore[arg-type]
    assert ok and bad is None

    # retroactively soften a verdict, leaving the digest in place: the chain must notice
    tampered = list(records)
    tampered[1] = tampered[1].model_copy(update={"verdict": "allow"})  # type: ignore[attr-defined]
    ok, bad = verify_chain(tampered)  # type: ignore[arg-type]
    assert not ok
    assert bad == "d-20"


def test_chain_detects_a_removed_record() -> None:
    records = []
    prev = None
    for n in (1, 20, 300):
        rec = _record(n, prev)
        records.append(rec)
        prev = rec.record_digest  # type: ignore[attr-defined]
    ok, bad = verify_chain([records[0], records[2]])  # type: ignore[arg-type]
    assert not ok
    assert bad == "d-300"


def test_digest_covers_prev_so_reordering_breaks_it() -> None:
    a = chain_digest(None, {"x": 1})
    b = chain_digest("deadbeef", {"x": 1})
    assert a != b


def test_direction_is_part_of_the_record() -> None:
    """Two decisions that differ only in direction must not share a digest."""
    ceiling = Ceiling(unit=Unit.PRINCIPALS, bands=(Band(above=10, verdict=Verdict.BLOCK),))
    digests = set()
    for direction in (Direction.EXACT, Direction.UPPER_BOUND):
        d = decide(
            ProposedCall(tool="t"),
            (("/g", "x", ceiling),),
            {"/g": Resolution.resolved(Unit.PRINCIPALS, 50, direction=direction)},
        )
        rec = build_record(
            d,
            decision_id="d",
            decided_at="2026-07-29T00:00:00Z",
            policy_digest="pol",
            code_version="0.1.0",
        )
        digests.add(rec.record_digest)
    assert len(digests) == 2


def test_canonical_bytes_is_utf8() -> None:
    assert canonical_bytes({"k": "é"}) == b'{"k":"\xc3\xa9"}'
