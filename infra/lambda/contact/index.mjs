/**
 * `neti-contact` — the Lambda that turns the contact form into an email.
 *
 * ## Why a Lambda at all
 *
 * The site runs on Vercel; SES lives in the Claritty AWS account. The obvious shortcut is to call
 * SES straight from the Vercel function, and it means minting an IAM user and putting a long-lived
 * `AWS_SECRET_ACCESS_KEY` into a third party's environment — a key that, by the nature of SES, can
 * send mail as `@mail.claritty.ai` to anyone in the world.
 *
 * This way the AWS permission never leaves AWS. It sits on this function's execution role, scoped
 * by `ses.json` to one `FromEmailAddress`. Vercel holds a shared secret that is useful for exactly
 * one thing: asking this function to send one message to one hardcoded recipient. If it leaks the
 * blast radius is spam to `CONTACT_TO`, not mail sent as Claritty to the world. That is the entire
 * argument for the extra hop, and it is the same argument the product this site sells makes about
 * blast radius.
 *
 * It also keeps the browser's CSP at `connect-src 'self'`. The page still only ever talks to its
 * own origin; the hop to AWS is server-to-server, so the header stays as narrow as it was.
 *
 * ## The guards
 *
 * A Function URL is on the public internet, so this is written as if the caller is hostile even
 * though the intended one is our own server:
 *
 * - **The recipient is `CONTACT_TO`, from the environment, never from the request.** Nothing the
 *   caller sends can redirect this mail. A compromised Vercel deployment cannot turn this into a
 *   relay, which is the point of putting the recipient here rather than passing it in.
 * - **The shared secret is compared in constant time, and a missing secret fails closed.** A
 *   `===` on a secret leaks its length and, given enough attempts, its contents. Defaulting to
 *   "no secret configured means allow" is how an endpoint ends up open after a bad deploy.
 * - **CR and LF are stripped from everything reaching a header**, lengths are capped, and there is
 *   a rate limit.
 *
 * The rate limit is per warm container and in memory. Lambda runs many containers, so a determined
 * sender gets more than `MAX_PER_WINDOW` through — a speed bump, not a control. Saying so is
 * cheaper than somebody later believing otherwise.
 */

import { createHmac, timingSafeEqual } from 'node:crypto'
import { SESv2Client, SendEmailCommand } from '@aws-sdk/client-sesv2'

const REGION = process.env.AWS_REGION || 'us-east-1'
const client = new SESv2Client({ region: REGION })

/** `mail.claritty.ai` is the verified domain; the execution role permits this address and no
 *  other, so changing it here without changing `ses.json` fails at SES rather than sending. */
const FROM = process.env.CONTACT_FROM || 'neti <noreply@mail.claritty.ai>'

const LIMITS = { name: 120, email: 200, org: 160, agents: 60, where: 400, want: 4000 }
const WINDOW_MS = 60 * 60 * 1000
const MAX_PER_WINDOW = 20
const seen = new Map()

const LOOKS_LIKE_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

function oneLine(value, max) {
  return (
    String(value ?? '')
      .replace(/[\r\n]+/g, ' ')
      // Every other C0 control too, not just the newlines. A tab or a vertical tab in a display
      // name is what SES rejects as "Local address contains control or whitespace", and the
      // visitor sees "could not send" for a name they typed in good faith.
      // eslint-disable-next-line no-control-regex
      .replace(/[\u0000-\u001F\u007F]/g, '')
      .trim()
      .slice(0, max)
  )
}

/**
 * A display name, made safe to sit in front of `<address>`.
 *
 * This was `${name} <${email}>` and it was wrong in a way that only shows up against real people.
 * SES parses the reply-to as an address list, so an unquoted name splits on the first comma: a
 * perfectly ordinary **"Smith, John"** becomes two malformed addresses, SES answers 400, and the
 * enquiry is lost with "Could not send" — the exact failure mode that is worst, because it happens
 * to real customers and never to a test.
 *
 * Found by an injection probe rather than by thinking about names: a hostile
 * `Eve\r\nBcc: victim@…` was correctly rejected by SES, and the same rejection was sitting under
 * every name containing a comma, a colon or a quote.
 *
 * So: printable ASCII becomes an RFC 5322 quoted-string with `\` and `"` escaped; anything else
 * becomes an RFC 2047 encoded-word, because a quoted-string may not hold raw UTF-8 and roughly
 * everybody outside the ASCII range has a name that needs it.
 */
function headerName(value) {
  if (/^[\x20-\x7E]*$/.test(value)) return `"${value.replace(/[\\"]/g, '\\$&')}"`
  return `=?UTF-8?B?${Buffer.from(value, 'utf8').toString('base64')}?=`
}

function multiLine(value, max) {
  return String(value ?? '')
    .replace(/\r\n/g, '\n')
    .trim()
    .slice(0, max)
}

/** Constant time, and length-independent: both sides are hashed first so a mismatch in length does
 *  not short-circuit and leak that fact. `timingSafeEqual` throws on unequal buffer lengths, which
 *  is exactly the leak it is meant to prevent — hashing removes the problem rather than catching
 *  the exception. */
function secretMatches(given, expected) {
  const digest = (value) => createHmac('sha256', 'neti-contact').update(String(value)).digest()
  return timingSafeEqual(digest(given), digest(expected))
}

/** The message body, and the only place the visitor's text is assembled. */
function compose(d, ip) {
  return [
    `Name:     ${d.name}`,
    `Email:    ${d.email}`,
    `Company:  ${d.org}`,
    `Agents:   ${d.agents || '—'}`,
    `Runs on:  ${d.where || '—'}`,
    '',
    'What a human should see before it happens:',
    d.want,
    '',
    `— neti.claritty.ai/cloud · ${ip}`,
  ].join('\n')
}

/** SES `Simple` content takes HTML or text or both. Both are sent: a text part so the message is
 *  readable in any client and survives quoting, and an HTML part so the reply-to and the wall of
 *  fields are legible in a normal inbox. `escape` matters — the visitor's own words go in here. */
function asHtml(text) {
  const escape = (s) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
  return `<pre style="font:14px ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap">${escape(text)}</pre>`
}

function reply(statusCode, body) {
  return {
    statusCode,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  }
}

function limited(ip) {
  const now = Date.now()
  const hits = (seen.get(ip) || []).filter((t) => now - t < WINDOW_MS)
  hits.push(now)
  seen.set(ip, hits)
  if (seen.size > 5000) {
    for (const [key, times] of seen) {
      if (!times.some((t) => now - t < WINDOW_MS)) seen.delete(key)
    }
  }
  return hits.length > MAX_PER_WINDOW
}

export async function handler(event) {
  const method = event?.requestContext?.http?.method
  if (method !== 'POST') return reply(405, { error: 'POST only' })

  const to = process.env.CONTACT_TO
  const secret = process.env.CONTACT_SECRET
  if (!to || !secret) {
    // Fails closed. An endpoint that treats "no secret configured" as "allow everyone" is an open
    // mailer one bad deploy away, and it would look healthy the whole time.
    console.error('contact: missing env', { CONTACT_TO: !!to, CONTACT_SECRET: !!secret })
    return reply(500, { error: 'not configured' })
  }

  const headers = event.headers || {}
  const given = headers['x-neti-secret'] || headers['X-Neti-Secret'] || ''
  if (!secretMatches(given, secret)) return reply(403, { error: 'forbidden' })

  let raw = event.body || ''
  if (event.isBase64Encoded) raw = Buffer.from(raw, 'base64').toString('utf8')

  let data
  try {
    data = JSON.parse(raw)
  } catch {
    return reply(400, { error: 'could not read that' })
  }

  const d = {
    name: oneLine(data.name, LIMITS.name),
    email: oneLine(data.email, LIMITS.email),
    org: oneLine(data.org, LIMITS.org),
    agents: oneLine(data.agents, LIMITS.agents),
    where: oneLine(data.where, LIMITS.where),
    want: multiLine(data.want, LIMITS.want),
  }
  if (!d.name || !d.email || !d.org || !d.want) return reply(400, { error: 'missing fields' })
  if (!LOOKS_LIKE_EMAIL.test(d.email)) return reply(400, { error: 'bad address' })

  const ip = oneLine(event?.requestContext?.http?.sourceIp || 'unknown', 64)
  if (limited(ip)) return reply(429, { error: 'too many' })

  const text = compose(d, oneLine(data.ip, 64) || ip)

  try {
    await client.send(
      new SendEmailCommand({
        // Our own verified sender. The visitor's address is the reply-to and never the from:
        // a stranger's address in `From` is what SPF and DMARC exist to reject, and using one
        // would put the whole `claritty.ai` sending reputation at risk over a contact form.
        FromEmailAddress: FROM,
        Destination: { ToAddresses: [to] },
        ReplyToAddresses: [`${headerName(d.name)} <${d.email}>`],
        Content: {
          Simple: {
            Subject: { Data: `neti cloud — ${d.org}`, Charset: 'UTF-8' },
            Body: {
              Text: { Data: text, Charset: 'UTF-8' },
              Html: { Data: asHtml(text), Charset: 'UTF-8' },
            },
          },
        },
      }),
    )
  } catch (err) {
    console.error('contact: SES rejected the send', err)
    return reply(502, { error: 'send failed' })
  }

  return reply(200, { ok: true })
}
