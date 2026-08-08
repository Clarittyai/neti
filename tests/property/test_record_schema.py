"""The record schema may grow. What it may never do is invalidate a record already written.

`neti verify` recomputes every link from the fields the record stores, so the set of fields the
digest covers *is* the schema. Add a key to it unconditionally and every record sealed before that
change recomputes to a different digest — `verify` then reports tampering on a file nobody touched,
which is the worst possible false alarm an audit surface can raise and the fastest way to make a
customer stop believing the rest of it.

So v2 covers its new field only for records that say they are v2. This file is the guard on that,
and on the reason v2 exists at all: a `--demo` record used to be byte-indistinguishable from a
measured one.

It is also the guard on the case that got past a version number. `redacted` entered the payload with
no schema bump, so v1 splits into two wire formats — sealed-before and sealed-after — that `schema_`
cannot tell apart, and 140 records in this repository's own log read as CHAIN BROKEN until
verification learned to fall back through the older shape. Hence the second rule these tests hold: a
record has to keep verifying under the payload it was *sealed* over, and a shape nobody bumped a
version for is still a shape somebody's file is written in.
"""

from __future__ import annotations

from neti.core.record import SCHEMA, SCHEMA_V1, DecisionRecord, chain_digest, verify_chain


def a_record(**overrides: object) -> DecisionRecord:
    base: dict[str, object] = {
        "decision_id": "d1",
        "decided_at": "2026-01-01T00:00:00+00:00",
        "tool": "remove_group_members",
        "args": {"group": "g-eng-all"},
        "verdict": "block",
        "rule": "/group:magnitude>200",
        "mode": "enforce",
        "policy_digest": "abc123",
        "code_version": "0.1.0",
    }
    return DecisionRecord.model_validate({**base, **overrides})


def test_a_v1_record_still_verifies_under_v2_code() -> None:
    """The compatibility claim, stated as the thing that would actually go wrong.

    A record sealed by an older release carries `schema: neti.decision.v1`, and the digest in it was
    computed over the fields v1 covered. New code must recompute exactly those fields for that
    record — not the v2 set — or an operator upgrading `neti` would find their entire existing chain
    reported as broken.
    """
    sealed = a_record(schema=SCHEMA_V1).sealed(None)
    ok, bad = verify_chain([sealed])
    assert ok, f"a v1 record failed to verify under v2 code, at {bad}"
    assert "synthetic" not in sealed.chained, "v1 must not gain a field it was never sealed over"


def test_a_v1_chain_and_a_v2_chain_both_verify_end_to_end() -> None:
    """Whole chains, because a per-record check would miss a `prev_digest` that stopped linking."""
    for schema in (SCHEMA_V1, SCHEMA):
        first = a_record(schema=schema, decision_id="a").sealed(None)
        second = a_record(schema=schema, decision_id="b").sealed(first.record_digest)
        ok, bad = verify_chain([first, second])
        assert ok, f"{schema} chain broke at {bad}"


def test_a_record_sealed_before_redacted_existed_still_verifies() -> None:
    """The real 0.1.0 wire format, which no version number distinguishes.

    Records written by the first release have no `redacted` key at all. Recomputing them with one —
    `"redacted": []`, a value the digest never covered — made `neti verify` answer CHAIN BROKEN on
    140 records in this repository's own log, and would have said the same to anybody upgrading a
    0.1.0 install about their own history.

    Both cohorts say `neti.decision.v1`, so the schema version cannot separate them and the fix
    cannot be keyed on it.
    """
    payload = {
        "schema": SCHEMA_V1,
        "decision_id": "d1",
        "decided_at": "2026-01-01T00:00:00+00:00",
        "tool": "remove_group_members",
        "args": {"group": "g-eng-all"},
        "verdict": "block",
        "rule": "/group:magnitude>200",
        "mode": "enforce",
        "policy_digest": "abc123",
        "code_version": "0.1.0",
    }
    # Sealed the way 0.1.0 sealed it: over a payload with no `redacted` key at all.
    staged = DecisionRecord.model_validate(payload)
    legacy = staged.chained
    legacy.pop("redacted")
    old = staged.model_copy(update={"record_digest": chain_digest(None, legacy)})

    ok, bad = verify_chain([old])
    assert ok, f"a record sealed before `redacted` existed failed to verify, at {bad}"


def test_the_redaction_marker_is_still_unstrippable_where_it_was_sealed() -> None:
    """The compatibility path must not become a way to launder a record.

    Presence-keyed coverage would be worth nothing if deleting the key also deleted the evidence
    that it was there. It does not: a record sealed *with* `redacted: ["/token"]` matches neither
    the current payload nor the superseded one once the marker is gone, so the tamper is caught
    exactly as before the fix. Asserted at v1, where the fallback exists and could have excused it.
    """
    sealed = a_record(schema=SCHEMA_V1, redacted=("/token",)).sealed(None)
    assert sealed.chained["redacted"] == ["/token"]

    laundered = sealed.model_copy(update={"redacted": ()})
    ok, bad = verify_chain([laundered])
    assert not ok, "the redaction marker was stripped and the chain still verified"
    assert bad == sealed.decision_id


def test_the_synthetic_marker_is_inside_the_digest() -> None:
    """It has to be un-strippable, for the same reason `redacted` is.

    A marker outside the digest is annotation: anyone holding the file could delete it and pass
    invented magnitudes off as measured ones, and `neti verify` would keep saying the chain is
    intact. The whole value of marking a demo record is that the marking cannot be removed quietly.
    """
    sealed = a_record(synthetic=True).sealed(None)
    assert sealed.chained["synthetic"] is True

    stripped = sealed.model_copy(update={"synthetic": False})
    ok, bad = verify_chain([stripped])
    assert not ok, "the synthetic marker was removed and the chain still verified"
    assert bad == sealed.decision_id


def test_a_measured_and_a_synthetic_record_are_distinguishable() -> None:
    """The defect this version exists for.

    `--demo` resolves against the synthetic tenant and produces numbers that are exact, confident
    and entirely invented — 41,203 principals, `direction: exact` — sealed into the same chain by
    the same code. Nothing separated such a record from a real one, and the default records path is
    the one a real run uses, so a demo interleaves fabricated traffic with measured traffic. `neti
    report` would average the two and `neti propose` would derive ceilings partly from fiction.
    """
    measured = a_record().sealed(None)
    invented = a_record(synthetic=True).sealed(None)

    assert measured.synthetic is False
    assert invented.synthetic is True
    assert measured.record_digest != invented.record_digest


def test_new_records_are_written_at_the_current_schema() -> None:
    """A default that silently stayed on v1 would leave the marker outside the digest forever."""
    assert a_record().schema_ == SCHEMA
    assert SCHEMA != SCHEMA_V1


def test_the_recorded_code_version_is_the_version_that_shipped() -> None:
    """`code_version` is part of the record's answer to *which build decided this*.

    It was a second literal in `Engine`, agreeing with `pyproject.toml` by luck. Nothing compared
    them, so the next release would have shipped a gate stamping every sealed record with the
    version before it — an audit trail that misidentifies the code that produced it, which is the
    same class of defect as a synthetic record that says nothing about being synthetic.

    Read out of `pyproject.toml` rather than restated, for the reason this repo has learned three
    times: a second copy of a fact is how the fact goes stale.
    """
    import tomllib
    from pathlib import Path

    from neti import __version__
    from neti.engine import Engine

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]

    assert __version__ == declared, (
        f"neti.__version__ is {__version__} and pyproject says {declared}"
    )
    assert Engine.__dataclass_fields__["code_version"].default == declared, (
        "the engine stamps a different version into records than the one that shipped"
    )
