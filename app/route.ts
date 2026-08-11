/** `/` — the overview page, returned verbatim. See the note in `next.config.mjs` for why this is a
 *  route handler rather than a React page. */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

export const dynamic = 'force-static'

export function GET() {
  return page('index.html')
}

/** Shared by both pages. `force-static` means this runs at build time, so the read is a build-time
 *  read of a committed file, not a filesystem call on every request. */
export function page(...parts: string[]) {
  const html = readFileSync(join(process.cwd(), 'docs', ...parts), 'utf8')
  return new Response(html, {
    headers: {
      'content-type': 'text/html; charset=utf-8',
      // Belt and braces with `vercel.json`, which sets the security headers for every path. This
      // one is here because it is about *this* response rather than about the site.
      'cache-control': 'public, max-age=0, must-revalidate',
    },
  })
}
