"""The CSP in `vercel.json` names the exact blocks the built pages inline.

The landing page inlines its own styles, script and images on purpose — `tools/make_site.py` says
why: it has to work from a `file://` URL, an email attachment or a preview host with no relative
paths. That rules out `script-src 'self'`, and leaves two options.

One is `'unsafe-inline'`, which would be a poor thing for *this* product to ship: a page arguing
that a gate should compare against something a human declared, served with a header permitting
anything the page happens to contain.

The other is naming each block by hash, which is what `vercel.json` does. The cost is that the
hashes go stale the moment anybody edits a `<style>` or `<script>` block — and a stale hash does
not fail loudly. It silently blocks the page's own script in a visitor's browser while every local
check still passes. So this recomputes them.

Same shape as `test_media_is_current.py`: the artefact is generated, and the check is that the
committed copy is what the generator produces.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import make_site  # noqa: E402

PAGES = (REPO / "docs" / "index.html", REPO / "docs" / "cloud" / "index.html")
VERCEL = REPO / "vercel.json"


def declared(directive: str) -> set[str]:
    """Every `sha256-…` the CSP names for one directive."""
    header = next(
        h["value"]
        for h in json.loads(VERCEL.read_text(encoding="utf-8"))["headers"][0]["headers"]
        if h["key"] == "Content-Security-Policy"
    )
    part = next(p for p in header.split(";") if p.strip().startswith(directive))
    return set(re.findall(r"'(sha256-[^']+)'", part))


def inlined(tag: str) -> set[str]:
    found: set[str] = set()
    for page in PAGES:
        found.update(make_site.csp_hashes(page.read_text(encoding="utf-8"), tag))
    return found


@pytest.mark.parametrize(("tag", "directive"), [("script", "script-src"), ("style", "style-src")])
def test_the_csp_covers_what_the_page_actually_inlines(tag: str, directive: str) -> None:
    present, allowed = inlined(tag), declared(directive)

    assert present, f"no inline <{tag}> found — has the build changed shape?"
    assert present <= allowed, (
        f"the built pages inline a <{tag}> block the CSP does not name:\n  "
        + "\n  ".join(sorted(present - allowed))
        + "\n\nA visitor's browser would block it while every local check passed. Rerun the hash "
        "step and commit vercel.json."
    )


@pytest.mark.parametrize("directive", ["script-src", "style-src"])
def test_the_csp_names_nothing_the_pages_do_not_contain(directive: str) -> None:
    """A hash left behind after a block is deleted is a permission granted to nothing — harmless
    today, and a lie in a header somebody reads to decide whether to trust this."""
    tag = directive.split("-")[0]
    stale = declared(directive) - inlined(tag)

    assert not stale, "vercel.json names hashes no page inlines:\n  " + "\n  ".join(sorted(stale))


def test_the_policy_refuses_the_easy_way_out() -> None:
    """`'unsafe-inline'` would make every test above pass and the header meaningless."""
    header = next(
        h["value"]
        for h in json.loads(VERCEL.read_text(encoding="utf-8"))["headers"][0]["headers"]
        if h["key"] == "Content-Security-Policy"
    )
    assert "unsafe-inline" not in header
    assert "unsafe-eval" not in header
    assert "default-src 'none'" in header
