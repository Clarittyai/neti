"""`POSITION.md` says things about other people's products, so it has to be checkable.

Everything else this repository publishes is held to evidence: magnitudes come from resolvers,
coverage from a survey, the incident corpus from citations that were verified one at a time. A
competitive document is the one place where a claim can be *about somebody else* and therefore
cannot be re-derived from anything here — which makes it the easiest place for the project to end up
being wrong in public, and the hardest place to notice.

So the file carries its own rules and this asserts them:

1. **Every quoted claim names where it came from and when it was read.** A quote with no URL is
   hearsay, and one with no date is a claim about a page that has since changed.
2. **The file states when it was last verified**, and goes stale loudly rather than quietly. A
   competitive document that ages into being wrong is worse than none, because somebody will repeat
   it in a meeting.
3. **Every `NC-xx` it cites actually exists** in `SCOPE.md`. The argument leans on that table
   repeatedly; a reference to a row that has been renumbered is an argument nobody can follow.

What this deliberately does *not* check is whether the quotes are still accurate — no test can fetch
a vendor's marketing page and tell you they still mean it. That is what rule 2 is for: it forces a
person to look again rather than pretending the looking was automated.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POSITION = ROOT / "POSITION.md"
SCOPE = ROOT / "SCOPE.md"

MAX_AGE_DAYS = 365
"""How long a competitive claim may stand before somebody has to look again.

A year rather than a quarter. The intent is to force a re-read, not to break an unrelated pull
request every few months — and a test that fails too often gets deleted, which would leave the file
with no check at all. That is the same reasoning `on_unsized_risk` follows: a signal nobody can live
with is a signal nobody keeps.
"""

# `> — [label](https://…), read YYYY-MM-DD`, which is how every citation in the file is written.
CITATION = re.compile(r"^>\s*—\s*\[[^\]]+\]\((https?://[^)]+)\),\s*read (\d{4}-\d{2}-\d{2})\s*$")

# A blockquote that is a quotation rather than an attribution line or a pull-quote.
QUOTE_LINE = re.compile(r"^>\s*(?!—)\S")


def position_lines() -> list[str]:
    return POSITION.read_text(encoding="utf-8").splitlines()


def test_position_exists_and_states_when_it_was_verified() -> None:
    assert POSITION.exists(), "POSITION.md is referenced by the README and the artifact page"
    assert re.search(
        r"\*\*Verified on (\d{4}-\d{2}-\d{2})\.\*\*", POSITION.read_text(encoding="utf-8")
    ), "POSITION.md must open by saying when its claims were last checked against the sources"


def test_every_quoted_claim_carries_a_url_and_the_date_it_was_read() -> None:
    """A quote with no source is hearsay; one with no date is a claim about a page since changed."""
    lines = position_lines()
    orphans: list[str] = []

    for i, line in enumerate(lines):
        if not QUOTE_LINE.match(line):
            continue
        # Walk to the end of this blockquote and look for an attribution inside it.
        j = i
        while j < len(lines) and lines[j].startswith(">"):
            j += 1
        block = lines[i:j]
        cited = any(CITATION.match(b) or re.match(r"^>\s*—\s*\*ibid\.\*", b) for b in block)
        if not cited:
            orphans.append(f"line {i + 1}: {line.strip()[:70]}")
        # Skip past the block we just examined.

    assert not orphans, (
        "quoted claims with no source and date:\n  "
        + "\n  ".join(dict.fromkeys(orphans))
        + "\n\nEvery quote needs `> — [host/path](url), read YYYY-MM-DD`, or `> — *ibid.*` when it "
        "continues the citation above it."
    )


def test_at_least_one_citation_is_present_at_all() -> None:
    """Guards the guard. A regex that matched nothing would make the test above vacuous — which is
    how a check quietly stops checking, and this repository has caught that happening before."""
    found = [line for line in position_lines() if CITATION.match(line)]
    assert len(found) >= 3, f"expected several dated citations, found {len(found)}"


def test_the_competitive_claims_have_not_gone_stale() -> None:
    """A competitive document that ages into being wrong is worse than not having one.

    This fails with the passage of time and nothing else, which is the point: it is the only way to
    make "somebody re-read the sources" a step that cannot be skipped. Re-read the pages, update the
    quotes that moved, and set the date.
    """
    stamp = re.search(
        r"\*\*Verified on (\d{4}-\d{2}-\d{2})\.\*\*", POSITION.read_text(encoding="utf-8")
    )
    assert stamp is not None
    verified = date.fromisoformat(stamp.group(1))
    age = (datetime.now(UTC).date() - verified).days

    assert age <= MAX_AGE_DAYS, (
        f"POSITION.md was last verified {age} days ago ({verified}), past the {MAX_AGE_DAYS}-day "
        "limit it sets for itself. Re-read the vendor pages it quotes, correct anything that has "
        "moved, and update the `Verified on` line."
    )


@pytest.mark.parametrize(
    "reference", sorted(set(re.findall(r"NC-\d+", POSITION.read_text(encoding="utf-8"))))
)
def test_every_scope_reference_points_at_a_row_that_exists(reference: str) -> None:
    """The argument leans on the non-coverage table repeatedly. A dangling reference is an argument
    the reader cannot follow, and renumbering that table is exactly the kind of edit that leaves
    one behind."""
    assert f"**{reference}**" in SCOPE.read_text(encoding="utf-8"), (
        f"POSITION.md cites {reference}, which SCOPE.md does not define"
    )
