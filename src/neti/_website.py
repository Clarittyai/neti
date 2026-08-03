"""Where neti lives on the internet, in one place.

A URL repeated across a README, a `pyproject.toml`, a landing page and half a dozen CLI strings is a
URL that will be wrong in some of them. `tests/property/test_urls_agree.py` asserts that every neti
address written anywhere in this repository is one of the two below, so moving is an edit here and
nowhere else.

`WEBSITE` is a GitHub Pages address today because it is one that works without anyone buying a
domain first. Pointing it at a custom domain is a one-line change here plus a CNAME; nothing else in
the repository knows the difference.
"""

from __future__ import annotations

WEBSITE = "https://neti-gate.github.io/neti/"
"""The product site: what neti is, for someone who has not decided to install anything yet."""

REPOSITORY = "https://github.com/neti-gate/neti"
"""The source. Apache-2.0, all of it — see LICENSING.md."""

CLOUD_REPOSITORY = "https://github.com/neti-gate/neti-cloud"
"""The control plane. BUSL-1.1, and a separate distribution; not required to run the gate."""
