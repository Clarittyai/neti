/**
 * The site is a Next.js app so that the mail function can be a Next.js route handler.
 *
 * What it is *not* is a React rewrite of the pages. `site/page.html` and `site/cloud.html` are
 * hand-authored documents with inline `<style>` and inline `<script>` — the ceiling simulator, the
 * approval demo, the contact modal — and `tools/make_site.py` inlines every image as a `data:` URI
 * so a page works from a `file://` URL or an email attachment. Rendering that through React would
 * mean either rewriting ~250KB of tuned markup as components, or injecting it with
 * `dangerouslySetInnerHTML` — where **inline `<script>` never executes**, because scripts inserted
 * via innerHTML are inert by spec. Every live thing on the site would go quiet, and it would look
 * like a styling problem rather than a fatal one.
 *
 * So the pages are served by route handlers that return the built file verbatim, prerendered at
 * build time (`dynamic = 'force-static'`). The browser parses a real HTML document, the scripts
 * run, the CSP hashes stay valid because the bytes are unchanged, and nothing is prerendered by
 * React at all. The Next.js part of this project is one API route; the site rides along.
 */
const config = {
  // The route handlers read from `docs/`, which is outside the app directory. Tracing does not
  // follow a `readFileSync` it cannot see through, so the files are named explicitly. Without this
  // the build succeeds and the deployed page 500s, which is the worst combination.
  outputFileTracingIncludes: {
    '/': ['./docs/index.html'],
    '/cloud': ['./docs/cloud/index.html'],
  },
  poweredByHeader: false,
}

export default config
