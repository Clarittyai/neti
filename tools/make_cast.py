"""Build the animated terminal cast — the transcript, revealed a line at a time.

A launch post wants motion, and the usual way to get it is a screen recording: somebody runs the
commands, a tool captures pixels, and the result is a file nothing can check. It ages the way every
README screenshot ages, silently, and it is the largest thing on the page.

This is the same transcript `tools/make_media.py` already draws as a still, with each line given a
moment. It is generated from a file `tests/golden` pins byte for byte, so it inherits the property
the still exhibits have: **it cannot show something the product no longer prints.** Change the
wording and the golden suite fails; update the transcript and `test_media_is_current` fails until
this is re-run.

    just cast           # rewrite it
    just cast --check   # fail if it is stale

**Why a GIF, having first built an SVG.** The first version was an animated SVG — text rather than
frames, 12KB, diffable, and every property above with none of the weight. It renders as a still
image in a README. SVG loaded through `<img>` is rendered in a static mode by Chrome: the SMIL never
runs. Checked directly rather than assumed, with both forms of the same file on one page — as
`<object>` it was well into act two while as `<img>` it sat frozen on line one four seconds in. A
README can only use `<img>`, so the elegant artifact was a still picture wearing the word "cast".

A GIF animates inside `<img>` everywhere, which is the requirement. What it costs is the thing the
SVG had for free: a raster cannot fall back through a font-family stack, so the face has to be a
file and that file has to contain every character the transcript uses.

Space Mono was the first choice — the monospace sibling of the site's Space Grotesk. Its Google
subset has no U+2500, the box-drawing horizontal, which appears **258 times** in this transcript as
the rule across every act heading. The first GIF rendered them as a row of tofu, and it took looking
at the picture to notice, because nothing in the pipeline objects to a missing glyph. So the face is
JetBrains Mono, which is drawn for terminals and covers them, and `_assert_covered` now refuses to
build a cast containing a character the font cannot draw. A silent tofu is the same class of defect
as a stale screenshot: wrong, and invisible to everything except a person looking at it.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from io import BytesIO
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _media():  # type: ignore[no-untyped-def]
    """`tools/` is not a package, so the sibling is loaded by path — as the tests already do."""
    spec = importlib.util.spec_from_file_location("make_media", REPO / "tools" / "make_media.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # In `sys.modules` before executing: the dataclass in `make_media` resolves its annotations
    # against its own module, and a module that cannot find itself fails with a bare `NoneType`.
    sys.modules["make_media"] = module
    spec.loader.exec_module(module)
    return module


media = _media()

TRANSCRIPT = "demo_here_full"
"""The six acts, because the ask is "show how to use it" rather than "show the best moment".

`hook_block` is the arresting one — a call stopped with a number — and it is three lines, which is a
poster rather than a demonstration. This one runs on the viewer's own machine with no credentials
and no traffic, which is the part of neti that is hard to believe until it happens in front of you.
"""

FACE = REPO / "docs" / "media" / "fonts" / "jetbrains-mono-regular.woff2"
OUT = REPO / "docs" / "media" / "cast.gif"

CAP = 108  # the width `make_media` already uses for this transcript
SIZE = 13
LEADING = 20
PAD = 18

LINE_MS = 150
"""How long each line holds before the next appears. Blank lines take a quarter of it — a terminal
does not pause on them, and a cast that does reads as buffering rather than as typing."""

BLANK_MS = 40
HOLD_MS = 4000
"""Time on the finished frame before it loops. Without it the last act is never read: the eye
reaches the bottom exactly as the whole thing restarts."""


def _ttf() -> BytesIO:
    """The committed woff2, decompressed so FreeType will open it. Same trick as `make_card`."""
    from fontTools.ttLib import TTFont

    font = TTFont(FACE)
    font.flavor = None
    buf = BytesIO()
    font.save(buf)
    buf.seek(0)
    return buf


def _assert_covered(lines: list[tuple[str, bool]]) -> None:
    """Every character in the cast is one the face can actually draw.

    PIL renders a missing glyph as `.notdef` — an empty box — and reports nothing. The first build
    of this put 258 of them across the act headings and was otherwise perfect, which is exactly the
    kind of defect that ships.
    """
    from fontTools.ttLib import TTFont

    cmap = TTFont(FACE).getBestCmap()
    missing = sorted({ch for line, _ in lines for ch in line if ord(ch) not in cmap})
    assert not missing, (
        f"{FACE.name} cannot draw "
        + ", ".join(f"U+{ord(ch):04X} {ch!r}" for ch in missing)
        + " — the cast would render these as empty boxes and say nothing about it"
    )


def _lines() -> list[tuple[str, bool]]:
    """The transcript, wrapped exactly as the still exhibit wraps it."""
    text = (media.TRANSCRIPTS / f"{TRANSCRIPT}.txt").read_text(encoding="utf-8")
    raw = text.splitlines()

    command = raw[0] if raw and raw[0].startswith("$ ") else ""
    body = raw[1:] if command else list(raw)
    if body and body[0] == "[exit 0]":
        body = body[1:]

    columns = max(20, min(max((len(line) for line in raw), default=20), CAP))
    out: list[tuple[str, bool]] = []
    if command:
        out.extend((piece, True) for piece in media._wrap(command, columns))
    for line in body:
        out.extend((piece, False) for piece in media._wrap(line, columns))
    return out


def frames() -> tuple[list, list[int]]:  # type: ignore[type-arg]
    from PIL import Image, ImageDraw, ImageFont

    lines = _lines()
    _assert_covered(lines)
    font = ImageFont.truetype(_ttf(), SIZE)

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    advance = probe.textlength("M" * 100, font=font) / 100
    columns = max(len(line) for line, _ in lines)
    width = round(columns * advance + PAD * 2)
    height = round(len(lines) * LEADING + PAD * 2)

    ink = tuple(int(media.INK.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    dim = tuple(int(media.INK_DIM.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    surface = tuple(int(media.SURFACE.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))

    # Drawn cumulatively rather than as deltas: GIF's own optimiser finds the changed rectangle, and
    # a line appearing at the bottom of an otherwise identical frame is exactly the case it handles
    # well. Forty-four full frames compress to less than one screen recording of the same length.
    out, durations = [], []
    canvas = Image.new("RGB", (width, height), surface)
    draw = ImageDraw.Draw(canvas)
    for index, (line, is_command) in enumerate(lines):
        if line:
            draw.text(
                (PAD, PAD + index * LEADING), line, font=font, fill=dim if is_command else ink
            )
        out.append(canvas.copy())
        durations.append(BLANK_MS if not line.strip() else LINE_MS)
    durations[-1] = HOLD_MS
    return out, durations


def expected() -> bytes:
    images, durations = frames()
    buf = BytesIO()
    images[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,  # leave the previous frame in place; every frame only adds to it
    )
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if the cast is stale")
    args = ap.parse_args()

    data = expected()
    if OUT.exists() and OUT.read_bytes() == data:
        print("the cast is current")
        return 0
    if args.check:
        print(f"{OUT.relative_to(REPO)} is stale — run `just cast`", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(data)
    print(f"wrote {OUT.relative_to(REPO)}  ({len(data) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
