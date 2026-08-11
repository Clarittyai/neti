/**
 * `POST /api/contact` — the form on `/cloud`, delivered to a person.
 *
 * This is the only server-side code in the project. `neti` itself is a local gate with no server,
 * and the whole product works with this file deleted; it exists so that the one page asking people
 * to get in touch can actually reach somebody.
 *
 * **It is also a public, unauthenticated endpoint that sends email**, which is the most abusable
 * shape on the web. Everything below that looks like paranoia is load-bearing:
 *
 * 1. **The recipient is `CONTACT_TO`, from the environment, and is never read from the request.**
 *    This is the line separating a contact form from an open relay. A handler that mails
 *    `body.to` gets found by a scanner within days and used to send other people's mail from our
 *    domain — and the first anyone hears of it is `claritty.ai` on a blocklist and legitimate mail
 *    silently stopping.
 * 2. **CR and LF are stripped from every value that reaches a header.** Resend takes JSON rather
 *    than raw SMTP, so this is defence in depth rather than the only guard — but `reply_to` and
 *    `subject` do become headers downstream, and a name containing "\r\nBcc: …" is exactly the
 *    input that has broken mailers for thirty years.
 * 3. **The visitor's address is `reply_to`, never `from`.** `from` is our own verified domain.
 *    Putting a stranger's address in `from` is what SPF and DMARC exist to reject, and doing it
 *    gets the whole domain distrusted — including mail with nothing to do with this form.
 * 4. **A honeypot, and a rate limit.** The honeypot stops the bots that fill every input; the limit
 *    raises the cost of the ones that do not.
 *
 * The rate limit is per warm instance and in memory, which is worth stating plainly rather than
 * implying: Vercel runs several instances and recycles them, so a determined sender gets more than
 * `MAX_PER_WINDOW` through. It is a speed bump, not a control. A real one needs shared state — the
 * same conclusion `SCOPE.md` reaches about per-machine budgets, for the same reason.
 */

import { Resend } from 'resend'

/** Node, not Edge. Nothing here needs the edge runtime, and the Node one is the better-trodden
 *  path for a route that talks to a third-party SDK. */
export const runtime = 'nodejs'
/** Never prerendered, never cached. The two page routes are `force-static`; this one must not be. */
export const dynamic = 'force-dynamic'

/** Field caps. A contact form has no legitimate use for more, and unbounded strings are how a small
 *  endpoint becomes an expensive one. */
const LIMITS = {
  name: 120,
  email: 200,
  org: 160,
  agents: 60,
  where: 400,
  want: 4000,
} as const

const WINDOW_MS = 60 * 60 * 1000
const MAX_PER_WINDOW = 5
const seen = new Map<string, number[]>()

/** Deliberately loose. Validating an address by regex is a well-known tar pit — the only real check
 *  is sending to it, and over-tight patterns reject real addresses. This rejects the obviously-not
 *  -an-address and leaves the rest to the reply. */
const LOOKS_LIKE_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

/** Everything that can reach a mail header goes through this. See note 2 above. */
function oneLine(value: unknown, max: number): string {
  return String(value ?? '')
    .replace(/[\r\n]+/g, ' ')
    .trim()
    .slice(0, max)
}

function multiLine(value: unknown, max: number): string {
  return String(value ?? '')
    .replace(/\r\n/g, '\n')
    .trim()
    .slice(0, max)
}

function limited(ip: string): boolean {
  const now = Date.now()
  const hits = (seen.get(ip) ?? []).filter((t) => now - t < WINDOW_MS)
  hits.push(now)
  seen.set(ip, hits)
  // Unbounded growth is its own denial of service. Swept when the map gets large rather than on a
  // timer, because a serverless instance may be frozen between requests and a timer that never
  // fires is a leak dressed as a cleanup.
  if (seen.size > 5000) {
    for (const [key, times] of seen) {
      if (!times.some((t) => now - t < WINDOW_MS)) seen.delete(key)
    }
  }
  return hits.length > MAX_PER_WINDOW
}

function json(body: unknown, status: number) {
  return Response.json(body, { status })
}

export async function POST(request: Request) {
  const to = process.env.CONTACT_TO
  const key = process.env.RESEND_API_KEY
  if (!to || !key) {
    // Never leaks which one is missing. The sentence a visitor sees says nothing about our
    // configuration; the one in the log says everything, because that is where it is useful.
    console.error('contact: missing env', { CONTACT_TO: !!to, RESEND_API_KEY: !!key })
    return json({ error: 'The contact form is not configured yet.' }, 500)
  }

  let data: Record<string, unknown>
  try {
    data = (await request.json()) as Record<string, unknown>
  } catch {
    return json({ error: 'Could not read that.' }, 400)
  }

  // The honeypot. A field no human sees and no human fills, so anything that fills it is a bot. It
  // gets a 200 rather than an error on purpose: a rejection tells the sender what to change.
  if (oneLine(data.website, 200)) return json({ ok: true }, 200)

  const name = oneLine(data.name, LIMITS.name)
  const email = oneLine(data.email, LIMITS.email)
  const org = oneLine(data.org, LIMITS.org)
  const agents = oneLine(data.agents, LIMITS.agents)
  const where = oneLine(data.where, LIMITS.where)
  const want = multiLine(data.want, LIMITS.want)

  if (!name || !email || !org || !want) {
    return json({ error: 'Name, email, company and the last field are required.' }, 400)
  }
  if (!LOOKS_LIKE_EMAIL.test(email)) {
    return json({ error: 'That email address does not look right.' }, 400)
  }

  const ip = oneLine(
    request.headers.get('x-forwarded-for')?.split(',')[0] ?? 'unknown',
    64,
  )
  if (limited(ip)) {
    return json({ error: 'Too many messages from here. Try again later.' }, 429)
  }

  const text = [
    `Name:     ${name}`,
    `Email:    ${email}`,
    `Company:  ${org}`,
    `Agents:   ${agents || '—'}`,
    `Runs on:  ${where || '—'}`,
    '',
    'What a human should see before it happens:',
    want,
    '',
    `— neti.claritty.ai/cloud · ${ip}`,
  ].join('\n')

  try {
    const { error } = await new Resend(key).emails.send({
      // Our own verified sender, not the visitor's. See note 3.
      from: oneLine(process.env.CONTACT_FROM || 'neti@claritty.ai', 200),
      to,
      replyTo: `${name} <${email}>`,
      subject: `neti cloud — ${org}`,
      text,
    })
    // The SDK reports failure in the payload as well as by throwing, and a version that only
    // checked the throw would answer 200 to a message that was never accepted.
    if (error) throw new Error(`${error.name}: ${error.message}`)
  } catch (err) {
    // The visitor gets a sentence they can act on and no internals. The address is in it because
    // the point of failing gracefully here is that the enquiry still reaches us.
    console.error('contact: send failed', err)
    return json({ error: `Could not send. Please email ${to} directly.` }, 502)
  }

  return json({ ok: true }, 200)
}

/** Anything but POST. Without this a GET returns Next's 405 with an HTML body, and the form's
 *  error path expects JSON. */
export function GET() {
  return json({ error: 'POST only' }, 405)
}
