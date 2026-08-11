"""`app/` is a Next.js app with exactly one job on the server: send the contact form to a person.

Two separate things are asserted here, and they fail for different reasons.

**The mail route must not become an open relay.** A public, unauthenticated endpoint that sends
email is the most abusable shape on the web, and the two ways it goes wrong announce themselves to
nobody:

- *Open relay.* A handler that takes its recipient from the request body is found by a scanner
  within days and used to send other people's mail from our domain. The first anyone hears of it is
  `claritty.ai` on a blocklist and legitimate mail silently stopping.
- *Header injection.* A name containing `"\\r\\nBcc: victim@example.test"` has broken mailers for
  thirty years. Resend takes JSON rather than raw SMTP so this is defence in depth, but `reply_to`
  and `subject` still become headers downstream.

**The two page routes must keep returning the built files byte for byte.** This is the migration's
one real hazard. `site/*.html` are hand-authored documents carrying inline `<script>` — the ceiling
simulator, the approval demo, the contact modal — and the obvious Next.js idiom, a React page using
`dangerouslySetInnerHTML`, silently kills all of them: scripts inserted via innerHTML never execute,
by spec. The page would still render, so it would read as a styling regression rather than as every
interactive thing on the site being dead. It would also invalidate the CSP hashes in `vercel.json`,
because React would not reproduce the bytes exactly.

These are source assertions rather than a running test, because the app runs on Node and this suite
is Python. The behaviour *was* verified end to end during the migration, against a stand-in for
Resend's API: the wire payload carried `to` from the environment while the request's own `to`, `cc`
and `from` were ignored, and a CRLF in the name arrived flattened. What these tests protect is that
somebody editing later cannot quietly remove the guards — the same discipline as
`test_docs_are_true`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAIL = REPO / "app" / "api" / "contact" / "route.ts"
INDEX_ROUTE = REPO / "app" / "route.ts"
CLOUD_ROUTE = REPO / "app" / "cloud" / "route.ts"
VERCEL = REPO / "vercel.json"
PACKAGE = REPO / "package.json"
LOCK = REPO / "package-lock.json"


def code(path: Path) -> str:
    """The file with comments removed, so a sentence *about* a rule cannot satisfy the rule."""
    body = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    return re.sub(r"^\s*//.*$", "", body, flags=re.M)


# --------------------------------------------------------------------------- the mail route


def test_the_recipient_comes_from_the_environment_and_never_from_the_request() -> None:
    """The single line separating a contact form from an open relay."""
    body = code(MAIL)

    assert "process.env.CONTACT_TO" in body, (
        "the recipient is no longer read from CONTACT_TO — if it now comes from the request, this "
        "endpoint is an open relay"
    )
    # Every `to:` handed to the mailer must be the bare env-derived name. A `to: data.to`, or a
    # template string containing one, is the failure mode.
    for match in re.finditer(r"^\s*to:\s*([^\n,]+)", body, re.M):
        value = match.group(1).strip()
        assert value == "to", f"the recipient is `{value}`, which is not the fixed CONTACT_TO"

    for forbidden in ("data.to", "data.cc", "data.bcc", "body.to"):
        assert forbidden not in body, f"`{forbidden}` reaches the mailer — that is an open relay"

    assert not re.search(r"^\s*(cc|bcc):", body, re.M), (
        "the handler sets cc/bcc; every recipient must be the one fixed address"
    )


def test_everything_reaching_a_header_has_its_newlines_stripped() -> None:
    body = code(MAIL)

    assert re.search(r"replace\(\s*/\[\\r\\n\]\+?/g", body), (
        "nothing strips CR/LF any more; a name containing a newline reaches a header intact"
    )
    # The fields that land in reply_to and subject must go through the one-line scrubber rather than
    # being read raw. `want` is the message body and is allowed its newlines.
    for field in ("name", "email", "org"):
        assert re.search(rf"\b{field}\s*=\s*oneLine\(", body), (
            f"`{field}` reaches a mail header without passing through oneLine()"
        )


def test_the_visitors_address_is_a_reply_to_and_never_a_from() -> None:
    """A stranger's address in `from` is what SPF and DMARC exist to reject, and using one gets the
    whole sending domain distrusted — including mail with nothing to do with this form."""
    body = code(MAIL)
    sender = re.search(r"^\s*from:\s*([^\n]+)", body, re.M)
    assert sender, "the mail no longer sets `from`"
    assert "CONTACT_FROM" in sender.group(1), (
        f"`from` is {sender.group(1).strip()} — it must be our own verified sender"
    )
    assert "replyTo:" in body, "the visitor's address must be reachable, via replyTo"


def test_the_route_refuses_anything_but_post_and_bounds_what_it_accepts() -> None:
    body = code(MAIL)
    assert "export async function POST" in body, "the route no longer handles POST"
    assert "export function GET" in body and "405" in body, (
        "a GET must answer 405 as JSON; Next's own 405 has an HTML body the form cannot read"
    )
    assert "429" in body, "the rate limit has gone"
    assert "data.website" in body, "the honeypot field is no longer read"
    assert re.search(r"\.slice\(0,\s*max\)", body), "field lengths are no longer capped"


def test_a_provider_error_is_not_reported_as_success() -> None:
    """The Resend SDK reports failure in the returned payload as well as by throwing.

    A handler that only catches the throw answers `200 {ok:true}` to a message the provider never
    accepted — the visitor is told it arrived, and it did not. That is worse than an error.
    """
    body = code(MAIL)
    assert re.search(r"const \{\s*error\s*\}\s*=\s*await", body), (
        "the send no longer destructures `error` off the SDK result"
    )
    assert re.search(r"if \(error\) throw", body), (
        "an error returned in the payload is not turned into a failure, so it would be reported as "
        "a successful send"
    )


# --------------------------------------------------------------------------- the page routes


def test_the_pages_are_served_as_built_and_not_rendered_by_react() -> None:
    """The hazard this whole migration carried.

    `dangerouslySetInnerHTML` is the idiomatic way to put existing markup into a React page, and it
    would leave every inline `<script>` on the site inert — scripts inserted via innerHTML do not
    execute. The demos would go quiet while the page still looked fine, and the CSP hashes in
    `vercel.json` would stop matching because React would not reproduce the bytes.
    """
    for route, expected in ((INDEX_ROUTE, "index.html"), (CLOUD_ROUTE, "cloud")):
        body = code(route)
        assert "force-static" in body, (
            f"{route.name} is no longer prerendered; the pages are static and must stay so"
        )
        assert expected in body, f"{route.name} no longer names the built file it serves"

    assert "dangerouslySetInnerHTML" not in code(INDEX_ROUTE) + code(CLOUD_ROUTE), (
        "a page is being injected into React, which makes every inline <script> on the site inert"
    )
    # The *call*, and the directory it reads from — not the identifier. Checking for `readFileSync`
    # alone was satisfied by the import statement, so gutting the body and returning a hardcoded
    # string still passed. An assertion a mutation walks through is not an assertion.
    assert re.search(r"readFileSync\(\s*join\([^;\n]*['\"]docs['\"]", code(INDEX_ROUTE)), (
        "the page is no longer read out of `docs/`, so `make_site.py` no longer decides what is "
        "served and the CSP hashes in vercel.json describe a file nobody sends"
    )


def test_the_build_is_told_to_carry_the_built_pages() -> None:
    """Next traces the files a function needs. It cannot see through a `readFileSync` of a path it
    computes, so `docs/` is named explicitly. Without it the build succeeds and the deployed page
    500s — the worst combination, because nothing local reproduces it."""
    config = (REPO / "next.config.mjs").read_text(encoding="utf-8")
    assert "outputFileTracingIncludes" in config
    for page in ("docs/index.html", "docs/cloud/index.html"):
        assert page in config, f"{page} is not traced into the build"


# --------------------------------------------------------------------------- the deploy


def test_the_csp_lets_the_page_reach_the_endpoint_and_nothing_else() -> None:
    """The form POSTs, so the page needs `connect-src`. It must stay `'self'`.

    This is the one place the header was deliberately widened, and it earns an assertion: a later
    `connect-src *` or an added analytics origin would make *no telemetry, no phone-home* in the
    page's own footer false, and nothing else in the suite would notice.
    """
    header = next(
        h["value"]
        for h in json.loads(VERCEL.read_text(encoding="utf-8"))["headers"][0]["headers"]
        if h["key"] == "Content-Security-Policy"
    )
    connect = next(p.strip() for p in header.split(";") if p.strip().startswith("connect-src"))
    assert connect == "connect-src 'self'", (
        f"connect-src is `{connect}`; the page must reach its own endpoint and no other origin, or "
        "the footer's 'no telemetry, no phone-home' stops being true"
    )


def test_the_deploy_is_a_next_app_with_the_dependency_it_needs() -> None:
    config = json.loads(VERCEL.read_text(encoding="utf-8"))
    assert config.get("framework") == "nextjs", (
        "vercel.json no longer declares the Next framework, so the route handlers are not built"
    )
    assert "outputDirectory" not in config, (
        "outputDirectory is left over from the static deploy; with it, Vercel serves docs/ and the "
        "API route never exists"
    )

    deps = json.loads(PACKAGE.read_text(encoding="utf-8"))["dependencies"]
    for needed in ("next", "react", "react-dom", "resend"):
        assert needed in deps, f"{needed} is not a dependency"
    assert LOCK.exists(), "no package-lock.json, so the build is not pinned"
