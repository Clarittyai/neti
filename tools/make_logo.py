"""Build neti's mark, at every size anything asks for it.

The product had no mark and no favicon. The console wore lucide's `ShieldCheck` — a padlock-adjacent
stock glyph that says "security product" and nothing else — and the site served the browser's
default globe, which is what a tab looks like when nobody has been there before.

**What the mark is.** One block, with a slot cut through it near the top. Below the slot is solid
accent: the part that fits. Above it is the same hue, uncommitted: the part that does not. That is
the entire product in two features — a magnitude, and the line it is measured against — and it is
the same sentence the landing page opens with.

Two features, not three, because the size that decides a mark is 16px. Three stacked elements is a
smudge there; the first two rounds of this drew a ceiling rule *between* two blocks and neither
survived. The slot is knocked out of a single silhouette rather than separating two shapes, so the
eye reads one object with something done to it instead of two objects near each other.

**Why it is code.** The SVG and the rasters are emitted from the constants below, so they cannot
drift: a mark redrawn by hand in three files is three marks. `tests/property/test_media_is_current`
already holds the generated SVGs to their source and this joins that arrangement.

    just logo           # rewrite every output
    just logo --check   # fail if any is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "media" / "logo"

# ---------------------------------------------------------------------------- geometry
#
# Expressed on a 32-unit grid, which is the SVG viewBox and divides evenly into every raster size
# anything asks for (16 / 32 / 48 / 180). A mark whose geometry lands on half-pixels at 16px is a
# mark with a soft edge in the one place softness is most visible.

GRID = 32
BOX = 26.0  # the block, centred: 3 units of air on every side
INSET = (GRID - BOX) / 2
RADIUS = 4.5  # not a pill. DESIGN.md reserves the full radius for things you press.
CUT_AT = 0.30  # where the slot sits, as a fraction of the block's height
GAP = 2.0  # the slot itself. At 16px this is exactly 1px — the floor for a visible cut.

ACCENT = (91, 127, 255)  # #5B7FFF, DESIGN.md's primary
OVER_ALPHA = 165
"""The tone above the slot: the same hue, at two thirds.

Alpha rather than a second hex on purpose. A favicon is composited onto whatever the browser's tab
strip happens to be, light or dark, and a fixed tone can only be right on one of them. At two thirds
the part that does not fit stays legible on both without ever competing with the part that does.
"""

_CUT = INSET + BOX * CUT_AT


def svg() -> str:
    """The mark, as the source both the site and the console inline."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}" '
        f'role="img" aria-label="neti">'
        f'<defs><clipPath id="b"><rect x="{INSET:g}" y="{INSET:g}" width="{BOX:g}" '
        f'height="{BOX:g}" rx="{RADIUS:g}"/></clipPath></defs>'
        f'<g clip-path="url(#b)" fill="rgb({ACCENT[0]},{ACCENT[1]},{ACCENT[2]})">'
        f'<rect x="0" y="0" width="{GRID}" height="{_CUT:g}" '
        f'opacity="{OVER_ALPHA / 255:.3f}"/>'
        f'<rect x="0" y="{_CUT + GAP:g}" width="{GRID}" height="{GRID - _CUT - GAP:g}"/>'
        f"</g></svg>"
    )


def raster(px: int, *, supersample: int = 16) -> Image.Image:
    """The same geometry, drawn at `px`.

    Supersampled and downsampled rather than drawn at the target size: PIL's `rounded_rectangle`
    aliases badly at 16px, and the slot — one pixel at the smallest size that matters — is exactly
    the feature that would vanish into it.
    """
    s = px * supersample
    k = s / GRID

    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle(
        [INSET * k, INSET * k, (INSET + BOX) * k, (INSET + BOX) * k], radius=RADIUS * k, fill=255
    )
    md.rectangle([0, _CUT * k, s, (_CUT + GAP) * k], fill=0)

    colour = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    cd = ImageDraw.Draw(colour)
    cd.rectangle([0, 0, s, _CUT * k], fill=(*ACCENT, OVER_ALPHA))
    cd.rectangle([0, (_CUT + GAP) * k, s, s], fill=(*ACCENT, 255))

    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    out.paste(colour, (0, 0), mask)
    return out.resize((px, px), Image.LANCZOS)


# `.ico` carries several sizes in one file and the browser picks. 16 is the tab, 32 is the bookmark
# bar and the Windows taskbar, 48 is the desktop shortcut. Rendering each rather than letting the
# encoder downscale one: the slot is a single pixel at 16 and survives being *drawn* there, not
# being resampled there.
ICO_SIZES = (16, 32, 48)
TOUCH = 180  # apple-touch-icon, the size iOS asks for


def outputs() -> dict[Path, bytes]:
    from io import BytesIO

    files: dict[Path, bytes] = {OUT / "mark.svg": svg().encode("utf-8")}

    buf = BytesIO()
    raster(TOUCH).save(buf, format="PNG")
    files[OUT / f"icon-{TOUCH}.png"] = buf.getvalue()

    # `append_images` rather than `sizes=` alone. Passing only `sizes` makes Pillow resample the one
    # image it was handed down to each entry, which is not what the comment above says and was not
    # what the first version of this file did — checked, and the 16px frame came back byte-identical
    # to a LANCZOS downscale of the 48. Every frame is now drawn at its own size, which for a
    # one-pixel slot is the difference between an edge and a grey smear.
    # Largest first, smaller ones appended. Pillow drops any requested size larger than the base
    # image it was handed, so seeding this with the 16 produced a single-entry icon that claimed
    # three — a favicon that looks right in a tab and is missing from the bookmark bar.
    frames = [raster(n) for n in sorted(ICO_SIZES, reverse=True)]
    buf = BytesIO()
    frames[0].save(
        buf,
        format="ICO",
        sizes=[(n, n) for n in sorted(ICO_SIZES)],
        append_images=frames[1:],
    )
    files[OUT / "favicon.ico"] = buf.getvalue()
    return files


def matches(path: Path, data: bytes) -> bool:
    """Whether the committed file is already this mark — compared as an *image*, not as bytes.

    The first version of this compared bytes, and it was red in CI for three commits. PNG and ICO
    go out through Pillow's encoder and zlib, neither of which promises the same bytes across
    versions or platforms: the files were generated on macOS, CI runs Linux, and the check demanded
    they be identical. The `.svg` is fine that way because it is text this module formats itself.

    Byte-equality was never the property worth holding. The mark is the pixels; a different zlib
    window that draws the same image is not a stale mark, and treating it as one means either
    committing churn on every machine that regenerates or a permanently failing check — which is
    how a staleness check gets switched off.
    """
    if not path.exists():
        return False
    if path.suffix == ".svg":
        return path.read_bytes() == data

    from io import BytesIO

    from PIL import Image

    committed = Image.open(BytesIO(path.read_bytes()))
    fresh = Image.open(BytesIO(data))
    if path.suffix == ".ico":
        if sorted(committed.ico.sizes()) != sorted(fresh.ico.sizes()):
            return False
        for size in sorted(fresh.ico.sizes()):
            committed.size, fresh.size = size, size
            if committed.convert("RGBA").tobytes() != fresh.convert("RGBA").tobytes():
                return False
        return True
    return committed.convert("RGBA").tobytes() == fresh.convert("RGBA").tobytes()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if any output is stale")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    stale = []
    for path, data in outputs().items():
        if matches(path, data):
            continue
        if args.check:
            stale.append(path.relative_to(REPO))
            continue
        path.write_bytes(data)
        print(f"wrote {path.relative_to(REPO)}  ({len(data) / 1024:.1f} KB)")

    if stale:
        print("stale — run `just logo`:", *(f"\n  {p}" for p in stale), file=sys.stderr)
        return 1
    if args.check:
        print("the mark is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
