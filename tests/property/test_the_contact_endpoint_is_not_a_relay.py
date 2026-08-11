"""`api/contact.js` is the only server-side code in this project, and it sends email.

A public, unauthenticated endpoint that sends mail is the most abusable shape on the web. The two
ways it goes wrong are well known and neither announces itself:

- **Open relay.** A handler that takes its recipient from the request body will be found by a
  scanner within days and used to send other people's mail from our domain. Nobody notices until
  the domain is on a blocklist and legitimate mail stops arriving.
- **Header injection.** SMTP headers are newline-delimited, so a name containing
  `"\\r\\nBcc: victim@example.test"` adds a recipient. Nodemailer guards this, and the handler
  strips CR/LF as well, so the guarantee does not rest on a transitive dependency's behaviour
  staying what it is today.

These are grep assertions rather than a running test, because the endpoint runs on Vercel's Node
runtime and this suite is Python — a full harness would mean a second toolchain in CI for one file.
The behaviour *was* verified against a real SMTP server during development (recipient fixed at
`CONTACT_TO` for every request including ones supplying `to` and `cc`; a CRLF+`Bcc:` name flattened
into a quoted display name with no extra envelope recipient). What these tests protect is the
property that somebody editing this file later cannot quietly remove those guards.

The equivalent discipline to `test_docs_are_true`: the claim in the source comment is checked
against the source rather than believed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENDPOINT = REPO / "api" / "contact.js"
VERCEL = REPO / "vercel.json"
PACKAGE = REPO / "package.json"
LOCK = REPO / "package-lock.json"


def source() -> str:
    return ENDPOINT.read_text(encoding="utf-8")


def code() -> str:
    """The file with its comments removed, so a sentence *about* a rule cannot satisfy the rule."""
    body = re.sub(r"/\*.*?\*/", "", source(), flags=re.S)
    return re.sub(r"^\s*//.*$", "", body, flags=re.M)


def test_the_recipient_comes_from_the_environment_and_never_from_the_request() -> None:
    """The single line separating a contact form from an open relay."""
    body = code()

    assert "process.env.CONTACT_TO" in body, (
        "the recipient is no longer read from CONTACT_TO — if it now comes from the request, this "
        "endpoint is an open relay"
    )
    # Every `to:` in a sendMail call must be the bare env-derived name. A `to: data.to`, a
    # `to: req.body.something`, or a template string containing one is the failure mode.
    for match in re.finditer(r"\bto:\s*([^\n,]+)", body):
        value = match.group(1).strip()
        assert value == "to", (
            f"sendMail's recipient is `{value}`, which is not the fixed CONTACT_TO"
        )

    for forbidden in ("data.to", "data.cc", "data.bcc", "body.to", "req.body.to"):
        assert forbidden not in body, f"`{forbidden}` reaches the mailer — that is an open relay"

    assert "cc:" not in body and "bcc:" not in body, (
        "the handler sets cc/bcc; every recipient must be the one fixed address"
    )


def test_everything_reaching_a_header_has_its_newlines_stripped() -> None:
    """Header injection, defended in this file rather than only in the dependency."""
    body = code()

    assert re.search(r"replace\(\s*/\[\\r\\n\]\+?/g", body), (
        "nothing strips CR/LF any more; a name containing a newline can add a Bcc"
    )
    # The fields that land in From / Reply-To / Subject must go through the one-line scrubber, not
    # be read raw. `want` is the message body and is allowed its newlines.
    for field in ("name", "email", "org"):
        assert re.search(rf"\b{field}\s*=\s*oneLine\(", body), (
            f"`{field}` reaches a mail header without passing through oneLine()"
        )


def test_the_visitors_address_is_a_reply_to_and_never_a_from() -> None:
    """Putting a stranger's address in `From` is what SPF and DMARC exist to reject, and it gets
    the whole sending domain distrusted — including mail that has nothing to do with this form."""
    body = code()
    from_line = re.search(r"\bfrom:\s*([^\n]+)", body)
    assert from_line, "sendMail no longer sets `from`"
    assert "CONTACT_FROM" in from_line.group(1) or "SMTP_USER" in from_line.group(1), (
        f"`from` is {from_line.group(1).strip()} — it must be our own authenticated mailbox"
    )
    assert "replyTo:" in body, "the visitor's address must be reachable, via replyTo"


def test_the_endpoint_refuses_anything_but_post_and_bounds_what_it_accepts() -> None:
    body = code()
    assert "req.method !== 'POST'" in body, "the endpoint no longer restricts the method"
    assert "405" in body and "429" in body, "the method guard or the rate limit has gone"
    assert "website" in body, "the honeypot field is no longer read"
    assert re.search(r"\.slice\(0,\s*max\)", body), "field lengths are no longer capped"


def test_the_csp_lets_the_page_reach_this_endpoint_and_nothing_else() -> None:
    """The form POSTs, so the page needs `connect-src`. It must stay `'self'`.

    This is the one place the header was deliberately widened, and it is worth an assertion: a
    later `connect-src *` or an added analytics origin would make `no telemetry, no phone-home` in
    the page's own footer false, and nothing else in the suite would notice.
    """
    header = next(
        h["value"]
        for h in json.loads(VERCEL.read_text(encoding="utf-8"))["headers"][0]["headers"]
        if h["key"] == "Content-Security-Policy"
    )
    connect = next(p.strip() for p in header.split(";") if p.strip().startswith("connect-src"))
    assert connect == "connect-src 'self'", (
        f"connect-src is `{connect}`; the page must be able to reach its own endpoint and no other "
        "origin, or the footer's 'no telemetry, no phone-home' stops being true"
    )


def test_the_function_can_actually_be_built() -> None:
    """`installCommand` used to be an `echo`, because there was nothing to install.

    There is now: the endpoint imports nodemailer, and a deploy that skips the install ships a
    function that throws on its first request — a failure nobody sees until an enquiry is lost.
    """
    config = json.loads(VERCEL.read_text(encoding="utf-8"))
    assert "npm" in config.get("installCommand", ""), (
        "vercel.json does not install dependencies, but api/contact.js imports nodemailer"
    )
    assert LOCK.exists(), "no package-lock.json, so `npm ci` cannot run and the build is not pinned"
    deps = json.loads(PACKAGE.read_text(encoding="utf-8"))["dependencies"]
    assert "nodemailer" in deps

    # Nodemailer 7 and earlier carry a CRLF-injection advisory against the very thing this endpoint
    # does. Pinning the major here means a careless `npm install nodemailer` downgrade fails a test
    # rather than quietly reintroducing it.
    major = int(re.sub(r"[^\d.]", "", deps["nodemailer"]).split(".")[0])
    assert major >= 9, (
        f"nodemailer is pinned to {deps['nodemailer']}; versions below 9 carry published SMTP "
        "command-injection and header-injection advisories"
    )
