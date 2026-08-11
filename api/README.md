# `api/` — the one piece of server-side code

`neti` itself is a local gate. It has no server, phones nothing home, and the whole product works
with this directory deleted. What lives here serves **neti.claritty.ai** and nothing else:
`contact.js` takes the form on `/cloud` and relays it over SMTP.

## Environment

Set these in the Vercel project (Settings → Environment Variables). Nothing here belongs in the
repository, and none of it is needed to run, test or develop `neti`.

| variable | what it is |
|---|---|
| `CONTACT_TO` | where enquiries go — `shahar@claritty.ai` |
| `SMTP_HOST` | your provider's SMTP host |
| `SMTP_PORT` | `587` for STARTTLS, `465` for implicit TLS. Defaults to `587` |
| `SMTP_USER` | the mailbox that authenticates |
| `SMTP_PASS` | its password or app password |
| `CONTACT_FROM` | optional; the `From` address. Defaults to `SMTP_USER` |

Until they are set the endpoint answers `500` with *"The contact form is not configured yet."* — the
form then shows that sentence and the address, so an enquiry is inconvenienced rather than lost.

**`CONTACT_FROM` must be an address the SMTP account is allowed to send as.** The visitor's address
goes in `Reply-To`, never in `From`: putting a stranger's address in `From` is what SPF and DMARC
exist to reject, and doing it gets the whole domain distrusted — including mail that has nothing to
do with this form.

## What the endpoint will not do

A public, unauthenticated endpoint that sends mail is the most abusable shape on the web, so these
are guarantees rather than intentions, and
`tests/property/test_the_contact_endpoint_is_not_a_relay.py` fails the build if one is removed:

- **The recipient is `CONTACT_TO` and is never read from the request.** A handler that mails
  `req.body.to` is an open relay; it gets found by a scanner within days and the first anyone hears
  of it is the domain landing on a blocklist.
- **CR and LF are stripped from everything that reaches a header.** SMTP headers are
  newline-delimited, so a name containing `\r\nBcc: …` would otherwise add a recipient.
- **Fields are length-capped, a honeypot field is checked, and there is a per-IP rate limit.**

The rate limit is per warm instance and in memory. Vercel runs several instances and recycles them,
so a determined sender gets more than five through — it is a speed bump, not a control. A real one
needs shared state, which is the same conclusion `SCOPE.md` reaches about per-machine budgets, for
the same reason.

`nodemailer` is pinned to **9 or later**: 7 and earlier carry published SMTP command-injection and
header-injection advisories against exactly what this does.

## Why the CSP has `connect-src 'self'`

The rest of the site is served under `default-src 'none'`. The form needs to POST, so `connect-src`
was widened — to `'self'` and no further. The page may talk to this endpoint and to no other origin,
which is what keeps *no telemetry, no phone-home* in the page footer true. There is a test on that
exact string.
