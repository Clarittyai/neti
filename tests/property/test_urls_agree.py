"""Every neti address in this repository is one of the three in `neti._website`.

A URL repeated across a README, a `pyproject.toml`, a landing page, some issue templates and a
handful of CLI strings is a URL that will be wrong in some of them — and the ones that go wrong are
the ones nobody clicks, which is to say the ones a stranger clicks first.

So there is one place they are written down, and this asserts that nothing disagrees with it. Moving
the site is then an edit to `src/neti/_website.py` and nothing else, and a typo in a link is a test
failure rather than a 404 somebody finds in six months.

This deliberately does not check that the addresses *resolve*. A test that reaches the network fails
on aeroplanes and in CI sandboxes, and this repository's whole posture is that its checks run with
the network unplugged. What is asserted here is internal agreement, which is the part that rots.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from neti._website import CLOUD_REPOSITORY, REPOSITORY, WEBSITE

REPO = Path(__file__).resolve().parents[2]

KNOWN = {WEBSITE, REPOSITORY, CLOUD_REPOSITORY}

# Any URL that mentions neti and is not obviously somebody else's.
NETI_URL = re.compile(r"https?://[^\s\"'`<>()\[\],]*neti[^\s\"'`<>()\[\],]*")

# Files a stranger reads, plus the packaging metadata a tool reads.
SEARCHED = (
    "README.md",
    "LICENSING.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "SCOPE.md",
    "pyproject.toml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/resolver_request.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
)


def _normalise(url: str) -> str:
    """A trailing slash and a `blob/main/...` suffix are the same address for this purpose."""
    url = url.rstrip("/.,);")
    for known in KNOWN:
        if url.startswith(known.rstrip("/")):
            return known
    return url


def test_the_files_this_checks_all_exist() -> None:
    """Guards against a rename turning this file into a no-op."""
    missing = [name for name in SEARCHED if not (REPO / name).exists()]
    assert not missing, f"listed in SEARCHED but not in the repository: {missing}"


@pytest.mark.parametrize("name", SEARCHED)
def test_every_neti_url_is_one_we_know_about(name: str) -> None:
    text = (REPO / name).read_text(encoding="utf-8")
    strays = sorted({url for url in NETI_URL.findall(text) if _normalise(url) not in KNOWN})
    assert not strays, (
        f"{name} names a neti address that is not in src/neti/_website.py:\n  "
        + "\n  ".join(strays)
        + "\n\nAdd it there if it is real, or fix it here. One place, so moving is one edit."
    )
