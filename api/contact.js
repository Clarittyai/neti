/**
 * The contact endpoint. One job: take the form on /cloud and send it to us over SMTP.
 *
 * This is the only server-side code in the project, and it is a **public, unauthenticated endpoint
 * that sends email**, which is the single most abusable shape on the web. Everything below that
 * looks like paranoia is load-bearing:
 *
 * 1. **The recipient is `CONTACT_TO`, from the environment, and is never read from the request.**
 *    This is the line that separates a contact form from an open relay. A handler that mails
 *    `req.body.to` will be found by a scanner within days and used to send other people's spam from
 *    our domain, and the first anyone hears of it is the domain landing on a blocklist.
 * 2. **CR and LF are stripped from every value that reaches a header.** SMTP headers are
 *    newline-delimited, so a name containing "\r\nBcc: victim@…" adds a recipient. Nodemailer
 *    guards this too; doing it here as well costs one function and means the guarantee does not
 *    depend on a transitive dependency's current behaviour.
 * 3. **A honeypot, and a rate limit.** Both are weak on their own. The honeypot stops the bots that
 *    fill every input; the limit raises the cost of the ones that do not.
 *
 * The rate limit is per warm instance and in memory, which is worth stating plainly rather than
 * implying: Vercel runs several instances and recycles them, so a determined sender gets more than
 * `MAX_PER_WINDOW` through. It is a speed bump, not a control. A real one needs shared state — the
 * same conclusion `neti` itself reaches about per-machine budgets in SCOPE.md, for the same reason.
 */

import nodemailer from 'nodemailer'

/** Field caps. A contact form has no legitimate use for more than this, and unbounded strings are
 *  how a small endpoint becomes an expensive one. */
const LIMITS = { name: 120, email: 200, org: 160, agents: 60, where: 400, want: 4000 }

const WINDOW_MS = 60 * 60 * 1000
const MAX_PER_WINDOW = 5
const seen = new Map()

/** Deliberately loose. Address validation by regex is a well-known tar pit — the only real check is
 *  sending to it, and over-tight patterns reject real addresses. This rejects the obviously-not-an
 *  -address and leaves the rest to the reply. */
const LOOKS_LIKE_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

/** Everything that can reach a mail header goes through this. See note 2 above. */
function oneLine(value, max) {
  return String(value ?? '')
    .replace(/[\r\n]+/g, ' ')
    .trim()
    .slice(0, max)
}

function body(value, max) {
  return String(value ?? '')
    .replace(/\r\n/g, '\n')
    .trim()
    .slice(0, max)
}

function limited(ip) {
  const now = Date.now()
  const hits = (seen.get(ip) || []).filter((t) => now - t < WINDOW_MS)
  hits.push(now)
  seen.set(ip, hits)
  // Unbounded growth is its own denial of service. The map is swept whenever it gets large rather
  // than on a timer, because a serverless instance may be frozen between requests and a timer that
  // never fires is a leak that looks like a cleanup.
  if (seen.size > 5000) {
    for (const [key, times] of seen) {
      if (!times.some((t) => now - t < WINDOW_MS)) seen.delete(key)
    }
  }
  return hits.length > MAX_PER_WINDOW
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST')
    return res.status(405).json({ error: 'POST only' })
  }

  const to = process.env.CONTACT_TO
  const { SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS } = process.env
  if (!to || !SMTP_HOST || !SMTP_USER || !SMTP_PASS) {
    // Never leaks which one is missing. The message a visitor sees says nothing about our
    // configuration; the one in the log says everything, because that is where it is useful.
    console.error('contact: missing env', {
      CONTACT_TO: !!to,
      SMTP_HOST: !!SMTP_HOST,
      SMTP_USER: !!SMTP_USER,
      SMTP_PASS: !!SMTP_PASS,
    })
    return res.status(500).json({ error: 'The contact form is not configured yet.' })
  }

  const data = typeof req.body === 'string' ? safeParse(req.body) : req.body || {}
  if (!data) return res.status(400).json({ error: 'Could not read that.' })

  // The honeypot. A field no human sees and no human fills, so anything that fills it is a bot.
  // It gets a 200 rather than an error on purpose: a rejection tells the sender what to change.
  if (oneLine(data.website, 200)) return res.status(200).json({ ok: true })

  const name = oneLine(data.name, LIMITS.name)
  const email = oneLine(data.email, LIMITS.email)
  const org = oneLine(data.org, LIMITS.org)
  const agents = oneLine(data.agents, LIMITS.agents)
  const where = oneLine(data.where, LIMITS.where)
  const want = body(data.want, LIMITS.want)

  if (!name || !email || !org || !want) {
    return res.status(400).json({ error: 'Name, email, company and the last field are required.' })
  }
  if (!LOOKS_LIKE_EMAIL.test(email)) {
    return res.status(400).json({ error: 'That email address does not look right.' })
  }

  const ip = oneLine(
    (req.headers['x-forwarded-for'] || '').split(',')[0] || req.socket?.remoteAddress || 'unknown',
    64,
  )
  if (limited(ip)) {
    return res.status(429).json({ error: 'Too many messages from here. Try again later.' })
  }

  const transport = nodemailer.createTransport({
    host: SMTP_HOST,
    port: Number(SMTP_PORT || 587),
    // 465 is implicit TLS; 587 starts plaintext and upgrades with STARTTLS. Getting this backwards
    // fails to connect rather than sending in the clear, which is the safe way round.
    secure: Number(SMTP_PORT || 587) === 465,
    auth: { user: SMTP_USER, pass: SMTP_PASS },
  })

  try {
    await transport.sendMail({
      // `from` is our own authenticated mailbox, not the visitor's. Putting a stranger's address in
      // `from` is what SPF and DMARC exist to reject, and it gets the whole domain distrusted.
      // Their address goes in `replyTo`, which is what it is for — hitting reply reaches them.
      from: oneLine(process.env.CONTACT_FROM || SMTP_USER, 200),
      to,
      replyTo: `${name} <${email}>`,
      subject: `neti cloud — ${org}`,
      text: [
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
      ].join('\n'),
    })
  } catch (err) {
    // The visitor gets a sentence they can act on and no internals. The address is in it because
    // the whole point of this endpoint failing gracefully is that the enquiry still reaches us.
    console.error('contact: send failed', err)
    return res.status(502).json({ error: `Could not send. Please email ${to} directly.` })
  }

  return res.status(200).json({ ok: true })
}

function safeParse(text) {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}
