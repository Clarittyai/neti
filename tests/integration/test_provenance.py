"""Downstream of untrusted input — the axis magnitude is blind to.

A prompt injection is small at both ends. The ingest is one ticket; the payload can be one file. In
the LangChain demo the gate stopped `purge("customer_data")` because 2,240 exceeded a ceiling — and
would have allowed `purge("src/secrets.env")`, which is the same attack with a better target.

What this adds is not intelligence. It is one mechanical question — *has this session already read
something the operator declared untrusted?* — that an attacker cannot write the answer to. The
injected text can claim anything about being authorised; it cannot change the fact that the session
read `customer_data/` two calls ago.

Three properties hold and are asserted below:

1. **Escalate only.** The provenance bands are added to the gate's own, never substituted, so a
   mistake costs a confirmation and never a silent allow.
2. **A call cannot taint itself.** The read that ingests untrusted content is judged under the
   ordinary ceilings — otherwise the first read of any ticket is impossible.
3. **It latches.** There is no un-reading a stranger's file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from neti.config.policy import Policy
from neti.core.provenance import Provenance, matches, taints
from neti.core.types import ProposedCall
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.resolvers.filesystem import FilesystemResolver


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A support desk: 60 tickets a stranger wrote, and one file that matters.

    60 rather than 40 so `purge("customer_data")` is genuinely over the gate's own ceiling of 50 —
    the first version used 40 and two "this must still block" assertions were passing calls that
    were under the ceiling anyway, proving nothing.
    """
    (tmp_path / "customer_data").mkdir()
    for i in range(60):
        (tmp_path / "customer_data" / f"ticket_{i}.md").write_text("hi", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "secrets.env").write_text("STRIPE_KEY=sk_live", encoding="utf-8")
    return tmp_path


def engine_for(tree: Path, **provenance: Any) -> Engine:
    policy = Policy.model_validate(
        {
            "version": 1,
            "mode": Mode.ENFORCE,
            "provenance": provenance,
            "tools": {
                "read_files": {
                    "gate": {
                        "/pattern": {
                            "resolver": "fs.paths",
                            "bands": [{"above": 100, "verdict": "block"}],
                            "on_unresolved": "allow",
                        }
                    }
                },
                "purge": {
                    "gate": {
                        "/path": {
                            "resolver": "fs.paths",
                            "bands": [{"above": 50, "verdict": "block"}],
                            "on_unresolved": "block",
                        }
                    }
                },
            },
        }
    )
    return Engine(policy=policy, resolvers={"fs.paths": FilesystemResolver(root=tree)})


def fire(engine: Engine, tool: str, target: str, session: str = "s") -> str:
    arg = "pattern" if tool == "read_files" else "path"
    result = engine.gate(ProposedCall(tool=tool, args={arg: target}, session_id=session))
    return result.decision.verdict.name


# --------------------------------------------------------------------------- the attack it catches


def test_the_small_payload_that_magnitude_cannot_see(tree: Path) -> None:
    """The demo's attack, retargeted at one file. This is the case that motivated the feature.

    `purge("src/secrets.env")` is one object. It is under every ceiling anyone would write, and no
    amount of tuning the magnitude bands reaches it — `SCOPE.md` NC-02 says so. What reaches it is
    that the session read a stranger's ticket first.
    """
    naive = engine_for(tree)
    assert fire(naive, "read_files", str(tree / "customer_data" / "ticket_*.md")) == "ALLOW"
    assert fire(naive, "purge", str(tree / "src" / "secrets.env")) == "ALLOW", (
        "one object is under every ceiling — this is exactly the gap"
    )

    guarded = engine_for(
        tree,
        untrusted=[f"{tree}/customer_data/**"],
        bands=[{"above": 0, "verdict": "confirm"}],
    )
    assert fire(guarded, "read_files", str(tree / "customer_data" / "ticket_*.md")) == "ALLOW"
    assert fire(guarded, "purge", str(tree / "src" / "secrets.env")) == "CONFIRM", (
        "one object, but the session had already eaten something a stranger wrote"
    )


def test_a_clean_session_is_untouched(tree: Path) -> None:
    """The whole cost of this feature, and it has to be zero for work that never reads a ticket."""
    guarded = engine_for(
        tree,
        untrusted=[f"{tree}/customer_data/**"],
        bands=[{"above": 0, "verdict": "confirm"}],
    )
    assert fire(guarded, "read_files", str(tree / "src" / "*")) == "ALLOW"
    assert fire(guarded, "purge", str(tree / "src" / "secrets.env")) == "ALLOW"


# --------------------------------------------------------------------------- the three properties


def test_a_call_cannot_taint_itself(tree: Path) -> None:
    """Otherwise the first read of any ticket is impossible, which is the agent's entire job."""
    guarded = engine_for(
        tree,
        untrusted=[f"{tree}/customer_data/**"],
        bands=[{"above": 0, "verdict": "block"}],
    )
    assert fire(guarded, "read_files", str(tree / "customer_data" / "ticket_*.md")) == "ALLOW"


def test_the_tightening_only_ever_escalates(tree: Path) -> None:
    """Added to the gate's own bands, never substituted.

    A looser provenance band must not be able to *rescue* a call the declared ceiling already
    stopped — which is what "escalate only" has to mean for it to be safe to get wrong.
    """
    guarded = engine_for(
        tree,
        untrusted=[f"{tree}/customer_data/**"],
        bands=[{"above": 10_000, "verdict": "allow"}],
    )
    fire(guarded, "read_files", str(tree / "customer_data" / "ticket_1.md"))
    # 60 objects, over `purge`'s own `above: 50`. If the provenance bands *replaced* the gate's
    # own, this permissive one would rescue it; because they are added, it still blocks.
    assert fire(guarded, "purge", str(tree / "customer_data")) == "BLOCK"


def test_the_taint_latches_for_the_rest_of_the_session(tree: Path) -> None:
    guarded = engine_for(
        tree,
        untrusted=[f"{tree}/customer_data/**"],
        bands=[{"above": 0, "verdict": "confirm"}],
    )
    fire(guarded, "read_files", str(tree / "customer_data" / "ticket_1.md"))
    for _ in range(3):
        assert fire(guarded, "purge", str(tree / "src" / "secrets.env")) == "CONFIRM"


def test_sessions_do_not_contaminate_each_other(tree: Path) -> None:
    """One agent reading a ticket must not tighten the gate for everybody else on the machine."""
    guarded = engine_for(
        tree,
        untrusted=[f"{tree}/customer_data/**"],
        bands=[{"above": 0, "verdict": "confirm"}],
    )
    fire(guarded, "read_files", str(tree / "customer_data" / "ticket_1.md"), session="dirty")
    assert fire(guarded, "purge", str(tree / "src" / "secrets.env"), session="clean") == "ALLOW"
    assert fire(guarded, "purge", str(tree / "src" / "secrets.env"), session="dirty") == "CONFIRM"


def test_a_blocked_read_does_not_taint(tree: Path) -> None:
    """It never ran, so nothing was ingested. Tainting on a call the gate stopped would punish the
    session for an attack the gate already defeated."""
    guarded = engine_for(
        tree,
        untrusted=[f"{tree}/customer_data/**"],
        bands=[{"above": 0, "verdict": "confirm"}],
    )
    assert fire(guarded, "purge", str(tree / "customer_data")) == "BLOCK"
    assert fire(guarded, "purge", str(tree / "src" / "secrets.env")) == "ALLOW"


def test_nothing_changes_when_nothing_is_declared(tree: Path) -> None:
    """Additive: a policy written before this feature behaves exactly as it did."""
    plain = engine_for(tree)
    fire(plain, "read_files", str(tree / "customer_data" / "ticket_*.md"))
    assert fire(plain, "purge", str(tree / "src" / "secrets.env")) == "ALLOW"


# --------------------------------------------------------------------------- the record says why


def test_the_record_names_the_file_that_tainted_the_session(tree: Path) -> None:
    """ "This session is tainted" is an assertion. "It read ticket_1.md through read_files" is
    evidence, and it is what somebody reconstructing an incident actually needs."""
    guarded = engine_for(
        tree,
        untrusted=[f"{tree}/customer_data/**"],
        bands=[{"above": 0, "verdict": "confirm"}],
    )
    guarded.gate(
        ProposedCall(
            tool="read_files",
            args={"pattern": str(tree / "customer_data" / "ticket_1.md")},
            session_id="s",
        )
    )
    result = guarded.gate(
        ProposedCall(tool="purge", args={"path": str(tree / "src" / "secrets.env")}, session_id="s")
    )

    assert result.record.provenance is not None
    assert result.record.provenance["tool"] == "read_files"
    assert "ticket_1.md" in result.record.provenance["target"]


# --------------------------------------------------------------------------- the glob vocabulary


@pytest.mark.parametrize(
    ("target", "pattern", "hit"),
    [
        ("customer_data/t.md", "customer_data/**", True),
        ("customer_data/a/b/t.md", "customer_data/**", True),
        ("customer_data/t.md", "customer_data", True),
        ("src/app.py", "customer_data/**", False),
        # `*` must not span a separator, or a rule naming one directory silently names a tree.
        ("customer_data/a/b.md", "customer_data/*", False),
        ("customer_data/b.md", "customer_data/*", True),
        ("mail/x.eml", "**/*.eml", True),
        ("./customer_data/t.md", "customer_data/**", True),
    ],
)
def test_the_pattern_vocabulary(target: str, pattern: str, hit: bool) -> None:
    assert (matches(target, (pattern,)) is not None) is hit


def test_a_tool_can_be_untrusted_whatever_its_argument() -> None:
    """`fetch_url("https://…")` has no path for a glob to name, and its result is a stranger's."""
    prov = Provenance(tools=frozenset({"fetch_url"}))

    assert taints(prov, "fetch_url", ("https://evil.example/x",)) is not None
    assert taints(prov, "read_files", ("src/app.py",)) is None
