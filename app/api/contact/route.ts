/**
 * `POST /api/contact` — the form on `/cloud`, delivered to a person.
 *
 * This is the only server-side code in the project. `neti` itself is a local gate with no server,
 * and the whole product works with this file deleted; it exists so that the one page asking people
 * to get in touch can actually reach somebody.
 *
 * ## Where the mail is actually sent
 *
 * Not here. This validates, rate-limits, and forwards to a Lambda in the Claritty AWS account
 * (`infra/lambda/contact/`), which sends through SES as `noreply@mail.claritty.ai`.
 *
 * The shortcut would be to call SES straight from this function, and it would mean putting a
 * long-lived `AWS_SECRET_ACCESS_KEY` into Vercel — a key that by SES's nature can send mail as
 * `@mail.claritty.ai` to anyone on earth. The extra hop keeps that permission inside AWS on the
 * Lambda's execution role, scoped to one From address. What lives on Vercel is a shared secret
 * whose entire power is "ask that function to send one message to one address it already knows".
 * If it leaks, the damage is spam to our own inbox rather than mail sent as Claritty to the world.
 *
 * **The recipient is not configured here.** It lives on the Lambda, so nothing on the web side —
 * including a compromised deployment of this app — can redirect where the mail goes.
 *
 * It also keeps the page's CSP at `connect-src 'self'`: the browser still only ever talks to its
 * own origin, and the hop to AWS is server to server.
 *
 * ## Why the guards are duplicated
 *
 * Every check here also exists in the Lambda. That is not an oversight. This layer rejects the
 * obvious cases without spending an invocation, and the Lambda repeats them because a Function URL
 * is on the public internet and must not depend on its caller having been careful.
 */

/** Node, not Edge. Nothing needs the edge runtime, and Node is the better-trodden path. */
export const runtime = 'nodejs'
/** Never prerendered, never cached. The two page routes are `force-static`; this must not be. */
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

/** Everything that can reach a mail header goes through this. The Lambda does it again. */
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
  const url = process.env.CONTACT_LAMBDA_URL
  const secret = process.env.CONTACT_LAMBDA_SECRET
  if (!url || !secret) {
    // Never leaks which one is missing. The sentence a visitor sees says nothing about our
    // configuration; the one in the log says everything, because that is where it is useful.
    console.error('contact: missing env', { url: !!url, secret: !!secret })
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

  const payload = {
    name: oneLine(data.name, LIMITS.name),
    email: oneLine(data.email, LIMITS.email),
    org: oneLine(data.org, LIMITS.org),
    agents: oneLine(data.agents, LIMITS.agents),
    where: oneLine(data.where, LIMITS.where),
    want: multiLine(data.want, LIMITS.want),
    ip: '',
  }

  if (!payload.name || !payload.email || !payload.org || !payload.want) {
    return json({ error: 'Name, email, company and the last field are required.' }, 400)
  }
  if (!LOOKS_LIKE_EMAIL.test(payload.email)) {
    return json({ error: 'That email address does not look right.' }, 400)
  }

  const ip = oneLine(request.headers.get('x-forwarded-for')?.split(',')[0] ?? 'unknown', 64)
  if (limited(ip)) {
    return json({ error: 'Too many messages from here. Try again later.' }, 429)
  }
  payload.ip = ip

  try {
    const sent = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-neti-secret': secret },
      body: JSON.stringify(payload),
      // A cold Lambda plus SES is a couple of seconds; a minute is not. Without a deadline this
      // request holds the function open until the platform kills it, and the visitor watches a
      // spinner the whole time rather than being told to copy the message instead.
      signal: AbortSignal.timeout(20_000),
    })
    if (!sent.ok) throw new Error(`the mailer answered ${sent.status}`)
  } catch (err) {
    // The visitor gets a sentence they can act on and no internals. The address is in it because
    // the point of failing gracefully here is that the enquiry still reaches us.
    console.error('contact: send failed', err)
    return json({ error: 'Could not send. Please email shahar@claritty.ai directly.' }, 502)
  }

  return json({ ok: true }, 200)
}

/** Anything but POST. Without this a GET returns Next's 405 with an HTML body, and the form's
 *  error path expects JSON. */
export function GET() {
  return json({ error: 'POST only' }, 405)
}
