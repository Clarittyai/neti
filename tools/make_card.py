"""Build the share card — the picture a link to neti turns into.

`document()` has been sending `twitter:card: summary_large_image` since the page was written, with
no `og:image` anywhere in the head. That is a promise of a large picture and nothing behind it: the
card renders as bare text, or the platform substitutes whatever it can scrape. Every other claim in
this repository is held to something; this one was not held to anything because nobody sees their
own link previews.

The card is drawn from the same constants as everything else — `make_logo` for the mark, the
committed Space Grotesk for the type, `DESIGN.md`'s accent and ground — so the thing a link expands
into is the thing the page looks like.

Needs fontTools to read the face: the committed file is `woff2`, which is a compressed wrapper
FreeType will not open, and PIL needs a plain TTF. That is a build-time dependency and not a runtime
one, so it is supplied the way `just dist` supplies twine rather than added to the project:

    uv run --with "fonttools[woff]" python tools/make_card.py
    uv run --with "fonttools[woff]" python tools/make_card.py --check
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from make_logo import ACCENT, raster  # noqa: E402

FACE = REPO / "docs" / "media" / "fonts" / "space-grotesk-latin.woff2"
CARD = REPO / "public" / "card.png"
"""`public/`, not `docs/media/` where the rest of the generated art lives, and the reason is that
this is the one image on the site that cannot be a `data:` URI. A scraper fetches `og:image` by URL
from its own machine — it never loads the page — so the card has to exist at an address. Next serves
`public/` from the root, which makes that address `<WEBSITE>card.png` with no route handler and no
`outputFileTracingIncludes` entry to keep in step."""

# 1200x630 is the size every platform crops toward, and the one Open Graph's own documentation
# names. Anything smaller gets upscaled by the scraper, which is where a crisp mark stops being one.
W, H = 1200, 630
PAD = 80

BG = (15, 15, 16)  # --bg
FG = (248, 250, 252)  # --fg
MUTED = (148, 163, 184)  # --fg-muted, one step up from the page's for legibility at card scale


def _ttf() -> BytesIO:
    """The committed woff2, decompressed so FreeType will open it.

    Decompressed rather than committing a second copy of the same face in a second format: two files
    is two things to keep in step, and the woff2 is the one the pages actually serve.
    """
    from fontTools.ttLib import TTFont

    font = TTFont(FACE)
    font.flavor = None  # drop the woff2 wrapper; the glyphs are unchanged
    buf = BytesIO()
    font.save(buf)
    buf.seek(0)
    return buf


def _font(size: int, weight: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(_ttf(), size)
    # A FreeType built without variable-font support still gives us the default instance, which is
    # a card in one weight rather than no card at all.
    with contextlib.suppress(OSError, AttributeError):
        f.set_variation_by_axes([weight])  # one variable file, 300-700
    return f


def _spaced(d: ImageDraw.ImageDraw, xy, text: str, font, fill, tracking: float) -> None:
    """Letter-spaced text, because PIL has no tracking and the label voice on this page is all
    tracking. Drawn per glyph, advancing by the glyph's own width plus the extra."""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + tracking


def render() -> Image.Image:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    # the mark and the wordmark, together, the way the nav carries them
    mark = raster(64)
    im.paste(mark, (PAD, PAD), mark)
    d.text((PAD + 84, PAD + 8), "neti", font=_font(44, 700), fill=FG)

    # the claim, at the scale the hero uses — the accent fragment is the same one the page colours
    head = _font(86, 700)
    d.text((PAD, 232), "Count what a tool call", font=head, fill=FG)
    d.text((PAD, 336), "will touch", font=head, fill=FG)
    run = d.textlength("will touch ", font=head)
    d.text((PAD + run, 336), "before it runs.", font=head, fill=ACCENT)

    # the hairline the whole page is built on, then the one thing a card has room to add
    d.rectangle([PAD, H - 148, W - PAD, H - 147], fill=(39, 39, 42))
    _spaced(d, (PAD, H - 116), "NETI.CLARITTY.AI", _font(22, 500), MUTED, 3.2)
    # Measured rather than placed: the first version hard-coded the offset for this exact string,
    # which is a right margin that silently stops being one the moment the words change.
    right = "0 MODELS IN THE PATH"
    label = _font(22, 500)
    width = sum(d.textlength(c, font=label) + 3.2 for c in right) - 3.2
    _spaced(d, (W - PAD - width, H - 116), right, label, MUTED, 3.2)
    return im


def expected() -> bytes:
    buf = BytesIO()
    render().save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if the card is stale")
    args = ap.parse_args()

    data = expected()
    if CARD.exists() and CARD.read_bytes() == data:
        print("the share card is current")
        return 0
    if args.check:
        print(f"{CARD.relative_to(REPO)} is stale — run `just card`", file=sys.stderr)
        return 1
    CARD.parent.mkdir(parents=True, exist_ok=True)
    CARD.write_bytes(data)
    print(f"wrote {CARD.relative_to(REPO)}  ({len(data) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
