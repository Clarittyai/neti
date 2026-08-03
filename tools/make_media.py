"""Render the golden transcripts to SVG, so the README can show what the product actually prints.

A README image is a claim, and image claims rot in a way prose claims do not: nobody diffs a PNG.
Every screenshot in every README is a photograph of a version of the software that may no longer
exist, and the reader cannot tell.

So these are not screenshots. Each one is a pure function of a file already under review:

    docs/media/<name>.svg  ==  render(tests/golden/transcripts/<name>.txt)

`tests/golden/` runs each command in a known state and pins its output byte for byte, precisely so
that a change to what an operator is told cannot land without somebody looking at it. Rendering
those transcripts inherits the property: change the wording and `tests/golden` fails; update the
transcript and `tests/property/test_media_is_current.py` fails until this script is re-run. There is
no path by which the picture and the program disagree.

    just media           # rewrite docs/media/*.svg
    just media --check   # fail if any of them is stale

**A clean frame, and no fake window.** This used to render through `rich.Console.export_svg`, which
draws a macOS title bar with three traffic lights around the output. That is a rejected pattern in
the Claritty design rules and it is worth stating why rather than just deleting it: the chrome is a
picture of an application that is not the one being shown. `neti` is not a terminal emulator, and
dressing its output up as one adds a window nobody has. What is left is the text on the surface it
belongs on, in a flat rounded frame.

The transcripts are monochrome because the CLI is monochrome. It prints plain text through
`typer.echo` and reaches for no colour at all. Nothing here adds any. A picture showing syntax
highlighting the terminal does not have would be a small lie of exactly the kind this repository
spends its time removing.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRANSCRIPTS = REPO / "tests" / "golden" / "transcripts"
MEDIA = REPO / "docs" / "media"

# `--surface-deep` from the Claritty platform tokens: the always-dark code surface, identical in
# light and dark themes on purpose, so a code block never changes colour under someone.
SURFACE = "#0d1117"
INK = "#c9d1d9"
INK_DIM = "#94a3b8"  # --surface-deep-foreground

FONT = (
    "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', "
    "'Courier New', monospace"
)
SIZE = 14
ADVANCE = SIZE * 0.6005  # monospace advance width, measured against SF Mono at this size
LEADING = SIZE * 1.55
PAD_X = 22
PAD_Y = 20
RADIUS = 12


@dataclass(frozen=True)
class Shot:
    """One transcript, and why it is worth a picture.

    `cap` is the widest the frame may be, not the width it will be. The actual width is the
    transcript's own longest line where that fits, so a table stays a table: `neti inventory` prints
    aligned columns, and wrapping them at a pleasing prose measure turns a table into a mess while
    still being a faithful rendering of the same characters. Above the cap, text wraps as it would
    in a terminal that narrow.
    """

    transcript: str
    caption: str
    cap: int = 100


# Order is the order they appear in the README. Each one answers a different question, because a
# second picture that answers the first one is decoration.
SHOTS = (
    Shot("hook_block", "The block: what a gated call looks like when it is too big"),
    Shot("demo_here_full", "The six acts, measured on one machine", cap=108),
    # Wide enough for the whole table: its longest row is 136 columns, and alignment is the point.
    Shot("inventory_rows", "What one credential can address, before any traffic", cap=140),
    Shot("propose_bimodal", "Ceilings derived from your own traffic, for a human to commit"),
    Shot("verify_intact", "The chain, offline"),
)


def _wrap(line: str, width: int) -> list[str]:
    """Break a line at `width` columns, on a space where there is one.

    A terminal wraps on the character; wrapping on the word reads better and is what the eye
    expects from a picture. Long unbroken runs (a JSON payload, a path) fall back to a hard break,
    because a word longer than the frame has to break somewhere.
    """
    if len(line) <= width:
        return [line]

    out: list[str] = []
    rest = line
    while len(rest) > width:
        cut = rest.rfind(" ", 0, width + 1)
        if cut <= 0:
            cut = width
        out.append(rest[:cut])
        rest = rest[cut:].lstrip(" ") if rest[cut : cut + 1] == " " else rest[cut:]
    if rest:
        out.append(rest)
    return out


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(text: str, *, cap: int) -> str:
    """Transcript in, SVG out. Deterministic: same bytes in, same bytes out, on any machine."""
    raw = text.splitlines()

    # Line 1 is the invocation and line 2 is `[exit N]`. The invocation stays: a picture of output
    # with no command above it is unattributable. The exit line is dropped when it is 0 and kept
    # when it is not, because a non-zero exit is part of what the picture is showing.
    command = raw[0] if raw and raw[0].startswith("$ ") else ""
    body = raw[1:] if command else list(raw)
    if body and body[0] == "[exit 0]":
        body = body[1:]

    columns = max(20, min(max((len(line) for line in raw), default=20), cap))
    lines: list[tuple[str, bool]] = []
    if command:
        lines.extend((piece, True) for piece in _wrap(command, columns))
    for line in body:
        # `_wrap("")` returns [""], so blank lines survive as blank lines.
        lines.extend((piece, False) for piece in _wrap(line, columns))

    width = round(columns * ADVANCE + PAD_X * 2)
    height = round(len(lines) * LEADING + PAD_Y * 2)

    rows = []
    for index, (line, is_command) in enumerate(lines):
        y = round(PAD_Y + LEADING * (index + 0.78), 2)
        fill = INK_DIM if is_command else INK
        rows.append(
            f'<text x="{PAD_X}" y="{y}" fill="{fill}" xml:space="preserve">{_escape(line)}</text>'
        )

    body_svg = "\n".join(rows)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" font-family="{FONT}" font-size="{SIZE}">\n'
        f'<rect width="{width}" height="{height}" rx="{RADIUS}" fill="{SURFACE}"/>\n'
        f"{body_svg}\n"
        "</svg>\n"
    )


def expected() -> dict[Path, str]:
    """Every image this repository should contain, and its exact contents."""
    out: dict[Path, str] = {}
    for shot in SHOTS:
        source = TRANSCRIPTS / f"{shot.transcript}.txt"
        if not source.exists():  # pragma: no cover — a typo in SHOTS, caught by the test
            raise SystemExit(f"no such transcript: {source.relative_to(REPO)}")
        out[MEDIA / f"{shot.transcript}.svg"] = render(
            source.read_text(encoding="utf-8"), cap=shot.cap
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if any image is missing or stale",
    )
    args = parser.parse_args()

    MEDIA.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []

    for path, svg in expected().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == svg:
            continue
        if args.check:
            stale.append(f"{path.relative_to(REPO)} — {'missing' if current is None else 'stale'}")
        else:
            path.write_text(svg, encoding="utf-8")
            print(f"wrote {path.relative_to(REPO)}")

    if stale:
        print("\n".join(stale), file=sys.stderr)
        print("\nrun `just media` and commit the result.", file=sys.stderr)
        return 1

    if args.check:
        print(f"{len(SHOTS)} image(s) current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
