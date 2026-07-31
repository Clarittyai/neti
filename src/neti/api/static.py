"""Serving the console's files from the Python package.

The console is a static export — every screen fetches the API in the browser and nothing renders
on a server — so it is a directory of files inside the wheel. `neti console` serves the API and the
UI from one process on one port, and installing the whole thing is `pipx install neti`.

The alternative was shipping a Node runtime beside a Python CLI and telling an operator to run two
servers on two ports. That is not a rounding error in adoption; it is the difference between a
security tool being evaluated and being closed.

Two behaviours worth stating:

**A missing export is not an error.** Someone on a source checkout has not built the web app, and
their API should still start — `neti serve` is a perfectly good way to work. `mount_console` reports
whether it found anything, and the caller says something useful either way.

**Unknown paths fall back to the export's own 404 page, never to `index.html`.** SPA fallbacks that
serve the app shell for everything turn a typo into a blank screen with a 200, which is indisputably
worse than a page that says the route does not exist. The one thing that must never be swallowed is
`/api/...`: an unmatched API path has to stay a JSON 404 from FastAPI, or a client debugging a
bad request gets served HTML and no useful signal at all.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

__all__ = ["CONSOLE_DIR", "console_dir", "mount_console"]

CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"


def console_dir() -> Path | None:
    """The built console inside the package, if this install has one."""
    index = CONSOLE_DIR / "index.html"
    return CONSOLE_DIR if index.is_file() else None


class _ExportFiles(StaticFiles):
    """`StaticFiles` that understands a Next.js export's directory-per-route layout.

    With `trailingSlash: true` every route is `<route>/index.html`, so `/audit` and `/audit/` are
    the same page and both have to work — a link that 404s over a slash is the sort of thing that
    reads as "this product is broken" long before anyone suspects the web server.
    """

    async def get_response(self, path: str, scope: object) -> Response:
        response = await super().get_response(path, scope)  # type: ignore[arg-type]
        if response.status_code == 404 and path not in ("", "."):
            nested = self.directory / path / "index.html"  # type: ignore[operator]
            if Path(nested).is_file():
                return FileResponse(nested)
        return response


def mount_console(app: FastAPI) -> Path | None:
    """Serve the built console at `/`. Returns where it came from, or `None` if absent.

    Mounted last and at the root, so every `/api/...` route registered before it still wins. FastAPI
    matches in registration order, which is the whole reason this is safe.
    """
    directory = console_dir()
    if directory is None:
        return None

    @app.get("/404", include_in_schema=False)
    async def not_found() -> FileResponse:
        return FileResponse(directory / "404.html")

    app.mount("/", _ExportFiles(directory=directory, html=True), name="console")
    return directory
