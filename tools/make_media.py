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

The transcripts are monochrome because the CLI is monochrome — it prints plain text through
`typer.echo` and reaches for no colour at all. Nothing here adds any. A picture that showed syntax
highlighting the terminal does not have would be a small lie of exactly the kind this repository
spends its time removing.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.terminal_theme import TerminalTheme
from rich.text import Text

REPO = Path(__file__).resolve().parents[1]
TRANSCRIPTS = REPO / "tests" / "golden" / "transcripts"
MEDIA = REPO / "docs" / "media"

# Ink on a dark ground, and nothing else. The foreground is the only colour that ever gets used,
# because the transcripts carry no styling to map.
THEME = TerminalTheme(
    (13, 17, 23),  # background — GitHub's dark canvas, so the image sits in the page
    (201, 209, 217),  # foreground
    [(13, 17, 23)] * 8,
    [(201, 209, 217)] * 8,
)


@dataclass(frozen=True)
class Shot:
    """One transcript, and why it is worth a picture.

    `cap` is the widest the rendered terminal may be, not the width it will be. The actual width is
    the transcript's own longest line where that fits, so a table stays a table — `neti inventory`
    prints aligned columns, and wrapping them at a pleasing prose measure turns a table into a mess
    while still being a faithful rendering of the same characters. Above the cap, text wraps exactly
    as it would in a terminal that narrow.
    """

    transcript: str
    caption: str
    cap: int = 100


# Order is the order they appear in the README. Each one has to answer a different question, because
# a second picture that answers the first one is decoration.
SHOTS = (
    Shot("hook_block", "The block: what a gated call looks like when it is too big"),
    Shot("demo_here_full", "The six acts, measured on one machine", cap=108),
    # Wide enough for the whole table: its longest row is 136 columns, and alignment is the point.
    Shot("inventory_rows", "What one credential can address, before any traffic", cap=140),
    Shot("propose_bimodal", "Ceilings derived from your own traffic, for a human to commit"),
    Shot("verify_intact", "The chain, offline"),
)


def render(text: str, *, cap: int) -> str:
    """Transcript in, SVG out. Deterministic: same bytes in, same bytes out, on any machine."""
    lines = text.splitlines()
    width = max(60, min(max((len(line) for line in lines), default=60) + 2, cap))

    # Line 1 is the invocation and line 2 is `[exit N]`. The invocation stays — a picture of output
    # with no command above it is unattributable. The exit line is dropped when it is 0 and kept
    # when it is not, because a non-zero exit is part of what the picture is showing.
    command = lines[0] if lines and lines[0].startswith("$ ") else ""
    body = lines[1:] if command else lines
    if body and body[0] == "[exit 0]":
        body = body[1:]

    console = Console(
        record=True,
        width=width,
        file=open("/dev/null", "w"),  # noqa: SIM115 — closed below
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
        highlight=False,
        soft_wrap=False,
    )
    try:
        if command:
            console.print(Text(command))
        for line in body:
            console.print(Text(line))
        return console.export_svg(title=command.removeprefix("$ ") or "neti", theme=THEME)
    finally:
        console.file.close()


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
