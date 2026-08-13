"""Build the landing page from `site/page.html` and the generated media.

Two outputs from one source, because they are wanted in two shapes and keeping two copies of a
landing page in sync by hand is how a landing page starts lying:

    docs/index.html    a complete document, which GitHub Pages serves from /docs
    build/page.html    the body alone, for publishing as a preview before anything is committed

`{{MEDIA:name}}` in the source is replaced by `docs/media/name.svg` as a base64 `data:` URI. Inlined
rather than linked for two reasons: the page then works from a file:// URL, an email attachment or a
preview host with no relative paths at all, and — the reason that matters — the images stay the ones
`tools/make_media.py` generates from the pinned transcripts. The landing page inherits the property
the README has: it cannot show something the product no longer prints.

    just site           # rewrite both outputs
    just site --check   # fail if either is stale

Regenerating is cheap and deterministic, so `tests/property/test_media_is_current.py` checks the
committed `docs/index.html` the same way it checks the SVGs.
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "site" / "page.html"
CLOUD_SOURCE = REPO / "site" / "cloud.html"
MEDIA = REPO / "docs" / "media"
CONSOLE = MEDIA / "console"
PAGE = REPO / "docs" / "index.html"
CLOUD_PAGE = REPO / "docs" / "cloud" / "index.html"
FRAGMENT = REPO / "build" / "page.html"
FONTS = MEDIA / "fonts"
LOGO = MEDIA / "logo"

PLACEHOLDER = re.compile(r"\{\{MEDIA:([a-z0-9_]+)\}\}")

# A third kind of asset, and the one with the hardest constraint. The page ships under
# `default-src 'none'`, so a font cannot be linked from anywhere — not a CDN, not a relative path,
# not the site's own origin without a `font-src 'self'` that would then also have to survive the
# page being opened from a file:// URL or an email attachment. Inlined as a `data:` URI it needs
# only `font-src data:`, and the page keeps the property every other asset here has: it carries
# what it needs and renders identically wherever it is opened.
#
# The licence travels with it. `docs/media/fonts/OFL.txt` is the SIL Open Font License the face is
# published under, and shipping the font without shipping that is the one thing the licence asks
# you not to do.
FONT_PLACEHOLDER = re.compile(r"\{\{FONT:([a-z0-9-]+)\}\}")

# The mark, from `tools/make_logo.py`. A placeholder rather than the SVG pasted into both pages:
# the geometry has one home, and `test_the_mark_is_what_its_geometry_builds_to` holds the file it
# comes from to that home. Pasting it would put the numbers in four places and a test in one.
LOGO_PLACEHOLDER = re.compile(r"\{\{LOGO:([a-z0-9-]+)\}\}")

# A second kind of image, kept syntactically distinct because it carries a weaker guarantee. The
# `{{MEDIA:…}}` SVGs are generated from transcripts the suite pins byte for byte and cannot show
# something the product no longer prints. These are screenshots of a running console, taken by hand;
# nothing can tell you automatically when they stop being true. `docs/media/console/PROVENANCE.md`
# records when each was taken and the exact commands that reproduce it, because the alternative is
# leaving somebody to discover the difference.
ATTR = re.compile(r'<([a-zA-Z][^>]*?)\s+style="([^"]*)"([^>]*?)>')
"""Every `style="…"` attribute on an element in the markup."""

CONSOLE_PLACEHOLDER = re.compile(r"\{\{CONSOLE:([a-z0-9_]+)\}\}")

TITLE = "neti — how big is this?"
DESCRIPTION = (
    "A preflight gate for agent tool calls. neti resolves what a call will actually touch before "
    "it runs, compares that count to a ceiling you declared, and stops it when it does not fit."
)


def _data_uri(path: Path, mime: str, hint: str) -> str:
    if not path.exists():
        raise SystemExit(f"{path.relative_to(REPO)} is missing — {hint}")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def fragment(source: Path = SOURCE) -> str:
    """The body: styles, markup and script, with every image inlined. No document wrapper."""
    text = source.read_text(encoding="utf-8")
    text = PLACEHOLDER.sub(
        lambda m: _data_uri(
            MEDIA / f"{m.group(1)}.svg",
            "image/svg+xml",
            "run `just media` first, since the page uses the same images the README does",
        ),
        text,
    )
    text = CONSOLE_PLACEHOLDER.sub(
        lambda m: _data_uri(
            CONSOLE / f"{m.group(1)}.png",
            "image/png",
            "see docs/media/console/PROVENANCE.md for how to re-take it",
        ),
        text,
    )
    text = LOGO_PLACEHOLDER.sub(
        lambda m: _data_uri(LOGO / f"{m.group(1)}.svg", "image/svg+xml", "run `just logo`"),
        text,
    )
    text = FONT_PLACEHOLDER.sub(
        lambda m: _data_uri(
            FONTS / f"{m.group(1)}.woff2",
            "font/woff2",
            "the face is committed under docs/media/fonts/ with the licence it ships under",
        ),
        text,
    )
    return hoist_inline_styles(text)


def hoist_inline_styles(body: str) -> str:
    """Move every `style="…"` attribute in the markup into the page's own stylesheet.

    **This fixes a defect that was live in production and invisible everywhere else.** The pages are
    served under a hash-based `style-src`, and a hash authorises a `<style>` *block* — it says
    nothing about a style *attribute*, which CSP governs separately through `style-src-attr`. So
    every one of the 52 inline styles on these two pages was being dropped by the browser: the
    verdict on the landing page rendered in body grey instead of red, and `margin-top: 1.25rem`
    computed to `0px` in forty-two places.

    Nothing local could see it. `next start` does not apply `vercel.json`, so the headers only exist
    on the deployed site, where the page still *looked* plausible — just wrong.

    The alternative was `style-src-attr 'unsafe-inline'`. It is a narrow relaxation and it would
    have worked, and it would also have put the word `unsafe-inline` into the header of the one page
    whose argument is that the strict thing is the workable thing. Hoisting costs a build step and
    keeps the claim true.

    Authors keep writing `style="…"` where it reads best — that is what it is for — and the shipped
    page has none. `test_the_built_pages_carry_no_inline_styles` asserts the output.
    """
    import re

    seen: dict[str, str] = {}

    def swap(match: re.Match[str]) -> str:
        before, decls, after = match.group(1), match.group(2).strip().rstrip(";"), match.group(3)
        if not decls:
            return f"<{before}{after}>"
        name = seen.setdefault(decls, f"h{len(seen)}")
        tag = f"<{before}{after}>"
        # Merge into an existing class attribute rather than adding a second one, which is invalid
        # and which browsers resolve by ignoring the later of the two.
        if re.search(r'\bclass="', tag):
            add = lambda c: f'class="{c.group(1)} {name}"'  # noqa: E731
            return re.sub(r'\bclass="([^"]*)"', add, tag, count=1)
        return f'<{before} class="{name}"{after}>'

    # Only the markup. A `style="…"` inside <script> is a template the page writes at runtime, and
    # rewriting it here would silently break the thing it builds.
    out, cursor = [], 0
    for block in re.finditer(r"<script\b.*?</script>", body, re.S):
        out.append(ATTR.sub(swap, body[cursor : block.start()]))
        out.append(block.group(0))
        cursor = block.end()
    out.append(ATTR.sub(swap, body[cursor:]))
    hoisted = "".join(out)

    if not seen:
        return hoisted

    # `!important`, and it is the faithful translation rather than a shortcut. An inline style
    # attribute outranks every rule in the stylesheet; a plain class does not, and the first
    # version of this hoist proved it — `.neti p { margin: 0 0 1rem }` is a class *and* an element,
    # so it beat the hoisted `.h5` and every `margin-top` on the page silently became `0`. Since
    # nothing in either stylesheet uses `!important`, marking these reproduces inline precedence
    # exactly: they win over the cascade and lose to nothing.
    def rule(decls: str, name: str) -> str:
        marked = "; ".join(d.strip() + " !important" for d in decls.split(";") if d.strip())
        return f"  .{name} {{ {marked}; }}"

    rules = "\n".join(rule(decls, name) for decls, name in seen.items())
    block = (
        '\n  /* ═════════════════════════════════ hoisted from `style="…"` by tools/make_site.py\n'
        "     Not hand-written and not to be edited here — each one's source is a style\n"
        "     attribute in `site/*.html`. They live in the stylesheet because a hash-based\n"
        "     `style-src` does not authorise style *attributes*, so inline ones are dropped\n"
        "     browser on the deployed site while working perfectly in every local check. */\n"
        f"{rules}\n</style>"
    )
    return hoisted.replace("</style>", block, 1)


def _icons() -> str:
    """The favicon, inlined the way every other asset on these pages is.

    A link to `/favicon.ico` would work on the deployed site and nowhere else — these documents are
    also opened from a `file://` path and from `build/page.html` with no server under them, which is
    the whole reason the images are data URIs. It also keeps the CSP as it is: a favicon is fetched
    under `img-src`, and `img-src 'self' data:` already covers it.

    The SVG is what modern browsers take, and it is the one that scales to a bookmark tile. The
    `.ico` is there for the ones that ask for `/favicon.ico` regardless of what the document says,
    and the 180px PNG is what iOS wants when somebody adds the page to a home screen.
    """
    svg = _data_uri(LOGO / "mark.svg", "image/svg+xml", "run `just logo`")
    ico = _data_uri(LOGO / "favicon.ico", "image/x-icon", "run `just logo`")
    touch = _data_uri(LOGO / "icon-180.png", "image/png", "run `just logo`")
    return (
        f'<link rel="icon" type="image/svg+xml" href="{svg}">\n'
        f'<link rel="alternate icon" type="image/x-icon" href="{ico}">\n'
        f'<link rel="apple-touch-icon" href="{touch}">'
    )


def document(body: str, *, title: str = "", description: str = "") -> str:
    """The same body inside a complete document, for a static host to serve."""
    title = title or TITLE
    description = description or DESCRIPTION
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0F0F10">
{_icons()}
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  html {{ -webkit-text-size-adjust: 100%; }}
  body {{ margin: 0; }}
  img {{ max-width: 100%; }}
</style>
</head>
<body>
<!-- Generated by tools/make_site.py from site/page.html. Edit that, then run `just site`. -->
{body}
</body>
</html>
"""


CLOUD_TITLE = "neti cloud — a confirm has to reach somebody"
CLOUD_DESCRIPTION = (
    "Approvals that reach a human — by webhook into Slack or Teams, or in the console. Org "
    "policy, fleet budgets and audit across every agent. The gate itself is Apache-2.0 and "
    "complete without any of it."
)


def csp_hashes(html: str, tag: str) -> list[str]:
    """`sha256-…` for every inline `<tag>` block in a built page.

    The page inlines its own styles, script and images on purpose — `fragment()` says why: it has to
    work from a `file://` URL, an email attachment or a preview host with no relative paths. That
    rules out `script-src 'self'`, and it would be an ugly thing for *this* product to ship
    `'unsafe-inline'` and hope nobody reads the header.

    So the inline blocks are hashed and named in `vercel.json` instead. A stale hash fails
    `test_the_csp_covers_what_the_page_actually_inlines` rather than silently blocking the page's
    own script in a browser, which is a failure a visitor would find before anybody here did.
    """
    import hashlib
    import re

    out: list[str] = []
    for block in re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.S):
        digest = hashlib.sha256(block.encode("utf-8")).digest()
        out.append("sha256-" + base64.b64encode(digest).decode("ascii"))
    return out


def expected() -> dict[Path, str]:
    """Every page this builds, and what each should contain.

    A dict rather than two named returns because `--check` and `main` both walk it, and a page that
    somebody adds to one and forgets in the other is a page that goes stale without anything saying
    so — which is the failure this whole file exists to prevent for the images.
    """
    body = fragment()
    pages = {PAGE: document(body), FRAGMENT: body}
    if CLOUD_SOURCE.exists():
        pages[CLOUD_PAGE] = document(
            fragment(CLOUD_SOURCE), title=CLOUD_TITLE, description=CLOUD_DESCRIPTION
        )
    return pages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="do not write; exit 1 if stale")
    args = parser.parse_args()

    stale: list[str] = []
    for path, text in expected().items():
        # build/ is a scratch output and is not committed, so --check only judges docs/index.html.
        if args.check and path == FRAGMENT:
            continue
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        if args.check:
            stale.append(f"{path.relative_to(REPO)} — {'missing' if current is None else 'stale'}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(REPO)}  ({len(text) // 1024} KB)")

    if stale:
        print("\n".join(stale), file=sys.stderr)
        print("\nrun `just site` and commit the result.", file=sys.stderr)
        return 1

    if args.check:
        print("docs/index.html is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
