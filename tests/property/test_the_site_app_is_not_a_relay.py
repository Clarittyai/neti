"""`app/` is a Next.js app with exactly one job on the server: send the contact form to a person.

Two separate things are asserted here, and they fail for different reasons.

**The mail route must not become an open relay.** A public, unauthenticated endpoint that sends
email is the most abusable shape on the web, and the two ways it goes wrong announce themselves to
nobody:

- *Open relay.* A handler that takes its recipient from the request body is found by a scanner
  within days and used to send other people's mail from our domain. The first anyone hears of it is
  `claritty.ai` on a blocklist and legitimate mail silently stopping.
- *Header injection.* A name containing `"\\r\\nBcc: victim@example.test"` has broken mailers for
  thirty years. The same handling protects a much more common case: an ordinary "Smith, John" is an
  address *list* to SES unless the display name is quoted.

**The two page routes must keep returning the built files byte for byte.** This is the migration's
one real hazard. `site/*.html` are hand-authored documents carrying inline `<script>` — the ceiling
simulator, the approval demo, the contact modal — and the obvious Next.js idiom, a React page using
`dangerouslySetInnerHTML`, silently kills all of them: scripts inserted via innerHTML never execute,
by spec. The page would still render, so it would read as a styling regression rather than as every
interactive thing on the site being dead. It would also invalidate the CSP hashes in `vercel.json`,
because React would not reproduce the bytes exactly.

Sending happens in `infra/lambda/contact/`, not here, and that is itself one of the properties
asserted below: calling SES from Vercel would mean a long-lived AWS key in a third party's
environment, able to send as `@mail.claritty.ai` to anyone on earth. On the Lambda the permission
stays in AWS, pinned by `ses.json` to one From address.

These are source assertions rather than a running test, because the app runs on Node and this suite
is Python. The behaviour *was* verified against real SES during development — a request carrying
its own `to` was delivered to `CONTACT_TO` and nobody else, and names containing a comma, an accent,
a quote and a CRLF `Bcc:` all produced a correctly quoted or encoded header. What these tests
protect is that somebody editing later cannot quietly remove the guards — the same discipline as
`test_docs_are_true`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAIL = REPO / "app" / "api" / "contact" / "route.ts"
LAMBDA = REPO / "infra" / "lambda" / "contact" / "index.mjs"
LAMBDA_POLICY = REPO / "infra" / "lambda" / "contact" / "ses.json"
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


def test_the_recipient_lives_on_the_lambda_and_never_comes_from_a_request() -> None:
    """The single line separating a contact form from an open relay.

    It is asserted on the Lambda rather than on the web route deliberately: the recipient is
    configured in AWS, so nothing on the web side — including a compromised deployment of the
    Next.js app — can redirect where this mail goes. The web route never sees an address at all.
    """
    body = code(LAMBDA)

    assert "process.env.CONTACT_TO" in body, (
        "the recipient is no longer read from CONTACT_TO — if it now comes from the request, this "
        "endpoint is an open relay"
    )
    destination = re.search(r"ToAddresses:\s*\[([^\]]*)\]", body)
    assert destination and destination.group(1).strip() == "to", (
        f"the recipient is `{destination.group(1).strip() if destination else 'gone'}`, which is "
        "not the fixed CONTACT_TO"
    )

    for forbidden in ("data.to", "data.cc", "data.bcc", "body.to"):
        assert forbidden not in body, f"`{forbidden}` reaches the mailer — that is an open relay"
    assert "CcAddresses" not in body and "BccAddresses" not in body, (
        "the handler sets cc/bcc; every recipient must be the one fixed address"
    )

    # And the web side must not have grown an address of its own to pass along.
    assert "CONTACT_TO" not in code(MAIL), (
        "the web function now knows a recipient; keeping it only in AWS is what stops a "
        "compromised Vercel deployment from redirecting the mail"
    )


def test_everything_reaching_a_header_has_its_newlines_stripped() -> None:
    """Both layers, because both build strings that end up in headers and the Lambda must not
    depend on its caller having been careful."""
    for path in (MAIL, LAMBDA):
        body = code(path)
        assert re.search(r"replace\(\s*/\[\\r\\n\]\+?/g", body), (
            f"{path.name} no longer strips CR/LF; a name containing a newline reaches a header"
        )
        # `want` is the message body and is allowed its newlines; these three become headers.
        for field in ("name", "email", "org"):
            assert re.search(rf"\b{field}:\s*oneLine\(", body), (
                f"`{field}` in {path.name} reaches a mail header without passing through oneLine()"
            )

    # The Lambda strips the rest of the C0 controls too. A tab in a display name is what SES
    # rejects as "Local address contains control or whitespace", losing a legitimate enquiry.
    assert "u0000" in code(LAMBDA), "control characters other than CR/LF are no longer removed"


def test_the_visitors_address_is_a_reply_to_and_never_a_from() -> None:
    """A stranger's address in `From` is what SPF and DMARC exist to reject, and using one gets the
    whole sending domain distrusted — including mail with nothing to do with this form."""
    body = code(LAMBDA)
    sender = re.search(r"FromEmailAddress:\s*([^\n,]+)", body)
    assert sender, "the mail no longer sets a From address"
    assert sender.group(1).strip() == "FROM", (
        f"`FromEmailAddress` is {sender.group(1).strip()} — it must be our own verified sender"
    )
    assert "ReplyToAddresses" in body, "the visitor's address must be reachable, via Reply-To"


def test_the_display_name_is_quoted_or_encoded_before_it_meets_an_address() -> None:
    """The bug a real customer would have found, not an attacker.

    `${name} <${email}>` is an address *list* to SES, so an ordinary "Smith, John" splits on the
    comma into two malformed addresses. SES answers 400, the route answers 502, and the enquiry is
    lost — for a name a fifth of the world has. It surfaced from an injection probe: the hostile
    `Eve\r\nBcc: …` was rejected by SES for the same reason the innocent name was.

    Printable ASCII becomes an RFC 5322 quoted-string; anything else an RFC 2047 encoded-word,
    because a quoted-string may not carry raw UTF-8 and most of the world's names need it.
    """
    body = code(LAMBDA)
    assert "function headerName" in body, "the display name is no longer quoted before use"
    assert re.search(r"ReplyToAddresses:\s*\[`\$\{headerName\(", body), (
        "the reply-to interpolates the raw name again; a comma in it splits the address list"
    )
    assert "=?UTF-8?B?" in body, "non-ASCII names are no longer encoded"


def test_the_lambda_is_the_only_thing_holding_the_aws_permission() -> None:
    """Why there is a Lambda at all rather than a call to SES from the web function.

    Calling SES from Vercel means a long-lived AWS key in a third party's environment — one that,
    by SES's nature, can send as `@mail.claritty.ai` to anyone on earth. Keeping the permission on
    the execution role, scoped to one From address, means the secret Vercel holds is only good for
    "send one message to one address you already know".
    """
    policy = json.loads(LAMBDA_POLICY.read_text(encoding="utf-8"))
    statement = policy["Statement"][0]
    assert statement["Action"] == ["ses:SendEmail"], "the role grants more than sending one email"
    assert statement["Condition"]["StringEquals"]["ses:FromAddress"], (
        "the policy no longer pins the From address, so the role can send as any verified identity"
    )

    web = code(MAIL)
    for forbidden in ("AWS_SECRET_ACCESS_KEY", "@aws-sdk", "SESv2Client"):
        assert forbidden not in web, (
            f"`{forbidden}` appears in the web function — the AWS permission has leaked out of AWS"
        )


def test_the_shared_secret_is_compared_in_constant_time_and_fails_closed() -> None:
    body = code(LAMBDA)
    assert "timingSafeEqual" in body, (
        "the constant-time comparison is gone; `===` on a secret leaks its length and, given "
        "enough attempts, its contents"
    )
    # The *call site*, not just the helper. A mutation that left `secretMatches` defined but
    # compared with `===` at the guard passed the version of this test that only looked for the
    # import — an unused safe function is not a safe comparison.
    assert re.search(r"if \(!secretMatches\(given, secret\)\)", body), (
        "the request guard no longer goes through the constant-time comparison"
    )
    assert re.search(r"if \(!to \|\| !secret\)", body), (
        "a missing secret no longer fails closed; an unconfigured deploy would accept anybody"
    )


def test_the_route_refuses_anything_but_post_and_bounds_what_it_accepts() -> None:
    body = code(MAIL)
    assert "export async function POST" in body, "the route no longer handles POST"
    assert "export function GET" in body and "405" in body, (
        "a GET must answer 405 as JSON; Next's own 405 has an HTML body the form cannot read"
    )
    assert "429" in body, "the rate limit has gone"
    assert "data.website" in body, "the honeypot field is no longer read"
    assert re.search(r"\.slice\(0,\s*max\)", body), "field lengths are no longer capped"


def test_a_mailer_error_is_not_reported_as_success() -> None:
    """`fetch` rejects only on a transport failure. A 500 from the mailer is a *resolved* promise,
    so a handler that does not check `ok` answers `200 {ok:true}` to a message that was never sent
    — the visitor is told it arrived, and it did not. That is worse than an error."""
    body = code(MAIL)
    assert re.search(r"if \(!sent\.ok\) throw", body), (
        "the response status is not checked, so a mailer error is reported as a successful send"
    )
    assert "AbortSignal.timeout" in body, (
        "there is no deadline on the call to the mailer; a hung request holds the function open "
        "until the platform kills it while the visitor watches a spinner"
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
    for needed in ("next", "react", "react-dom"):
        assert needed in deps, f"{needed} is not a dependency"
    # The AWS SDK is deliberately absent: the web side never talks to AWS, and a dependency on it
    # here would be the first sign that somebody moved the sending back into Vercel.
    assert not any(d.startswith("@aws-sdk") for d in deps), (
        "the web app depends on the AWS SDK; sending belongs in the Lambda, where the IAM role is"
    )
    assert LOCK.exists(), "no package-lock.json, so the build is not pinned"
