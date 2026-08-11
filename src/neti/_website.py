"""Where neti lives on the internet, in one place.

A URL repeated across a README, a `pyproject.toml`, a landing page and half a dozen CLI strings is a
URL that will be wrong in some of them. `tests/property/test_urls_agree.py` asserts that every neti
address written anywhere in this repository is one of the two below, so moving is an edit here and
nowhere else.

`WEBSITE` was a GitHub Pages address until 2026-08-11, chosen because it works without anyone
buying a domain first. It is a custom domain now — which took exactly what this docstring predicted:
one line here, a `docs/CNAME`, and nothing else in the repository noticed. `test_urls_agree.py` is
what made that true rather than hopeful.
"""

from __future__ import annotations

WEBSITE = "https://neti.claritty.ai/"
"""The product site: what neti is, for someone who has not decided to install anything yet."""

REPOSITORY = "https://github.com/Neti-Security/neti"
"""The source. Apache-2.0, all of it — see LICENSING.md."""

CLOUD_REPOSITORY = "https://github.com/Neti-Security/neti-cloud"
"""The control plane. BUSL-1.1, and a separate distribution; not required to run the gate."""
