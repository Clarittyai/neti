/** neti's mark.
 *
 * One block, with a slot cut through it near the top. Below the slot is solid: the part that fits.
 * Above it is the same hue, uncommitted: the part that does not. A magnitude and the line it is
 * measured against, which is the whole product.
 *
 * The console wore lucide's `ShieldCheck` before this — a stock glyph that says "security product"
 * and nothing more specific, and one that a dozen other tools in this category also wear.
 *
 * The geometry is `tools/make_logo.py`, which is what the site inlines and what the favicon is
 * drawn from. Copied rather than imported, on the same terms as everything else shared with the
 * landing page: `DESIGN.md` says copied-not-imported, and `test_the_console_mark_matches_the_site`
 * is the price of that — it holds these numbers to the ones the generator emits, so the two cannot
 * drift the way the cloud page's stylesheet did.
 *
 * `currentColor`, so the caller decides. On the accent circle in the sidebar it inherits the
 * foreground; anywhere on a plain ground, set `text-accent`.
 */
export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} role="img" aria-label="neti">
      <defs>
        <clipPath id="neti-mark">
          <rect x="3" y="3" width="26" height="26" rx="4.5" />
        </clipPath>
      </defs>
      <g clipPath="url(#neti-mark)" fill="currentColor">
        <rect x="0" y="0" width="32" height="10.8" opacity="0.647" />
        <rect x="0" y="12.8" width="32" height="19.2" />
      </g>
    </svg>
  );
}
