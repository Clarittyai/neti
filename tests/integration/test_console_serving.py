"""`neti console` — the API and the UI on one port.

The whole point is that installing this is `pipx install neti` and running it is one command. That
only holds if the Python process can serve a Next.js static export correctly, and there are exactly
two ways to get it wrong, both of which these tests pin down:

- **An unmatched `/api/...` path answered with HTML.** The console mount is a catch-all at `/`, so
  without a guard a typo'd endpoint returns the 404 *page* with a `text/html` content type, and a
  client debugging it gets a wall of markup instead of an error.
- **Routes that 404 over a trailing slash.** The export is a directory per route, and a link that
  breaks on `/audit` versus `/audit/` reads as a broken product long before anyone suspects the
  web server.

Skipped when the console has not been built, because a source checkout that has never run
`just console-sync` is a normal state to be in and not a failure.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import pytest

# Same shim `test_api.py` uses: starlette wants `httpx2` and warns loudly through `httpx`.
warnings.filterwarnings("ignore", message=".*starlette.testclient.*")

from fastapi.testclient import TestClient  # noqa: E402

from neti.api.app import create_app  # noqa: E402
from neti.api.state import build_state  # noqa: E402
from neti.api.static import console_dir  # noqa: E402
from tests.integration.test_inventory import EXAMPLE  # noqa: E402

needs_console = pytest.mark.skipif(
    console_dir() is None, reason="no built console — run `just console-sync`"
)


@pytest.fixture
def client(tmp_path: Path) -> Any:
    state = build_state(config=EXAMPLE, records=tmp_path / "decisions.ndjson", demo=True)
    with TestClient(create_app(state)) as c:
        yield c
    state.close()


def test_an_unknown_api_path_stays_json(client: Any) -> None:
    response = client.get("/api/definitely-not-a-route")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "no API route" in response.json()["detail"]


def test_real_api_routes_still_win_over_the_mount(client: Any) -> None:
    assert client.get("/api/state").json()["mode"] == "demo"
    assert client.get("/api/inventory").status_code == 200


@needs_console
@pytest.mark.parametrize(
    "path",
    ["/", "/gate/", "/decisions/", "/decision/", "/policy/", "/audit/", "/scorecard/", "/connect/"],
)
def test_every_console_route_is_served(client: Any, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@needs_console
def test_a_route_without_its_trailing_slash_still_resolves(client: Any) -> None:
    """Either served or redirected — what it must not do is 404."""
    response = client.get("/audit", follow_redirects=True)
    assert response.status_code == 200


@needs_console
def test_the_decision_page_takes_its_id_from_the_query(client: Any) -> None:
    """The reason the route is static at all: ids are decisions that have not happened yet."""
    assert client.get("/decision/?id=anything-at-all").status_code == 200


@needs_console
def test_an_unknown_page_is_a_404_not_the_app_shell(client: Any) -> None:
    """An SPA fallback would answer 200 with the shell and turn a typo into a blank screen."""
    assert client.get("/no-such-page").status_code == 404


def test_the_api_can_run_without_a_console(tmp_path: Path) -> None:
    """`neti serve` is a supported way to work, and it must not depend on a built UI."""
    state = build_state(config=EXAMPLE, records=tmp_path / "d.ndjson", demo=True)
    with TestClient(create_app(state, serve_console=False)) as bare:
        assert bare.get("/api/state").status_code == 200
        assert bare.get("/").status_code == 404
    state.close()
