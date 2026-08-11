# `neti-contact` — the contact form's mail path

`neti` itself is a local gate with no server. This directory exists so that the one page asking
people to get in touch can reach somebody, and it is the only server-side thing in the project.

```
browser ──POST /api/contact──▶ Next.js on Vercel ──POST + shared secret──▶ Lambda ──▶ SES ──▶ inbox
          (same origin only)     validates, rate-limits        (Function URL)   (execution role)
```

## Why the extra hop

The shortcut is to call SES straight from the Vercel function. That means minting an IAM user and
putting a long-lived `AWS_SECRET_ACCESS_KEY` into a third party's environment — a key that, by the
nature of SES, can send mail as `@mail.claritty.ai` **to anyone in the world**. A leak there is a
phishing campaign with Claritty's DKIM signature on it.

With the Lambda, the AWS permission never leaves AWS. It sits on the execution role, pinned by
`ses.json` to a single `ses:FromAddress`. What Vercel holds is a shared secret whose entire power is
*"ask that function to send one message to the one address it already knows"*. If it leaks, the
damage is spam to our own inbox.

**The recipient is configured on the Lambda, not on Vercel.** Nothing on the web side — including a
compromised deployment of the site — can redirect where this mail goes.

It also keeps the page's CSP at `connect-src 'self'`. The browser only ever talks to its own origin;
the hop to AWS is server to server, so the header stays as narrow as it was before the form existed.

## Deploying

```sh
CONTACT_SECRET=$(openssl rand -hex 32) ./deploy.sh
```

Idempotent: every step checks before it creates, so re-running after an edit to `index.mjs` is safe.
There is no `delete` anywhere in it — removing the function is a deliberate manual act.

It prints the Function URL. Put both of these in the Vercel project (Settings → Environment
Variables):

| variable | value |
|---|---|
| `CONTACT_LAMBDA_URL` | the Function URL the script prints |
| `CONTACT_LAMBDA_SECRET` | the `CONTACT_SECRET` you passed in |

What it creates, all in `us-east-1` unless `AWS_REGION` says otherwise:

- IAM role `neti-contact-lambda` — `ses:SendEmail` for one From address, plus the standard
  CloudWatch Logs policy
- Lambda `neti-contact` — Node 22, 256 MB, 15 s
- a Function URL with `AuthType NONE`

`AuthType NONE` is deliberate. IAM auth would mean giving Vercel an AWS key to sign requests with,
which is the thing this design exists to avoid. The door is guarded by the shared secret instead,
compared in constant time, and a missing secret **fails closed**.

## What is checked, and where

`tests/property/test_the_site_app_is_not_a_relay.py` fails the build if any of these is removed —
each one was mutation-tested, so an assertion that cannot fail does not count:

- the recipient comes from `CONTACT_TO` and never from a request
- CR, LF and the other C0 controls are stripped from everything reaching a header
- the display name is quoted or RFC 2047 encoded before it meets an address
- the secret comparison at the guard goes through `timingSafeEqual`
- the web function contains no AWS SDK and no AWS key

### The name bug, because it will look like an odd rule otherwise

The reply-to was `` `${name} <${email}>` ``. SES parses that as an address **list**, so an entirely
ordinary **`Smith, John`** splits on the comma into two malformed addresses; SES answers 400, the
route answers 502, and the enquiry is lost. It surfaced from an injection probe — a hostile
`Eve\r\nBcc: …` was rejected for exactly the same reason an innocent name was.

Printable ASCII now becomes an RFC 5322 quoted-string; anything else becomes an RFC 2047
encoded-word, because a quoted-string may not carry raw UTF-8 and most of the world's names need it.

## Account state, as of 2026-08-11

Checked rather than assumed, because both would have caused silent non-delivery:

- SES production access is **on** (`ProductionAccessEnabled: true`, 50,000/day) — not sandboxed, so
  mail reaches unverified recipients
- `mail.claritty.ai` is verified with `DkimAttributes.Status: SUCCESS`

## Limits worth knowing

The rate limit is **per warm container, in memory**. Lambda runs many containers and recycles them,
so a determined sender gets more than 20/hour through. It is a speed bump, not a control; a real one
needs shared state — the same conclusion `SCOPE.md` reaches about per-machine budgets, for the same
reason.
