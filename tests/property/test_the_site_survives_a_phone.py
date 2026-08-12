"""The landing page must not scroll sideways on a phone.

It did, twice, and neither time did anything here notice.

The mechanism both times was the same and it is not obvious. A grid that declares its columns only
inside `@media (min-width: …)` has **no** `grid-template-columns` below that breakpoint, so the
single implicit track is sized `auto` — which is `minmax(auto, max-content)`. The track grows to the
widest unwrapped thing inside it and drags the document with it. On `/` that was a monospace call
signature: the track went to 394px, the page to 414px, and the viewport was 375px.

The trap is that the obvious defence does nothing. `.cmd` is already `overflow-x: auto`, and a
scroll container still reports its full max-content to the track sizing it. Only a zero floor on the
track stops it — `minmax(0, 1fr)`.

The first fix put `minmax(0, 1fr)` in the *media queries*, because the two-column case was the one
visible on a desktop screen, and left every phone broken. So both halves are asserted here: a base
declaration must exist, and every flexible track must carry the floor.

**This is a static check and it is worth being honest about what that buys.** It cannot see a fixed
width, a wide image, or a `white-space: nowrap` heading — a real regression could still get past it.
Catching those needs a headless browser rendering at 390px, which is a dependency this repository
does not have and which would be the right next step if this class of bug appears a third time. What
this does catch is the exact mechanism that caused it twice, stated as a rule rather than as a
memory.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PAGES = (REPO / "docs" / "index.html", REPO / "docs" / "cloud" / "index.html")

#: `minmax(<anything>, …)`, so a track with an explicit minimum can be removed before looking for
#: the bare ones. `minmax(10rem, 2fr)` is fine — its floor is a length, not `auto`.
BOUNDED = re.compile(r"minmax\([^()]*\)")

#: What is left after that. A bare `1fr` is really `minmax(auto, 1fr)`: it refuses to go below its
#: content's min-content width, which is the whole bug.
BARE_FR = re.compile(r"\b\d*\.?\d*fr\b")


def stylesheet(page: Path) -> str:
    """Every inline `<style>` block, comments removed.

    Comments matter: the note explaining this rule contains the words it forbids, and a check that
    cannot tell a declaration from a sentence about one fails on its own documentation.
    """
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", page.read_text(encoding="utf-8"), re.S)
    return re.sub(r"/\*.*?\*/", "", "\n".join(blocks), flags=re.S)


def declarations(css: str) -> list[tuple[str, str, str]]:
    """`(selector, value, media)` for every `grid-template-columns` in the sheet."""
    found: list[tuple[str, str, str]] = []
    for media, body in _blocks(css):
        for selector, block in re.findall(r"([^{}]+)\{([^{}]*)\}", body):
            hit = re.search(r"grid-template-columns\s*:\s*([^;}]+)", block)
            if hit:
                found.append((selector.strip(), hit.group(1).strip(), media))
    return found


def _blocks(css: str) -> list[tuple[str, str]]:
    """Split into `(media, css)` pairs — the top level plus each `@media` body."""
    out: list[tuple[str, str]] = []
    rest = css
    for match in re.finditer(r"@media([^{]+)\{", css):
        depth, i = 1, match.end()
        while i < len(css) and depth:
            depth += (css[i] == "{") - (css[i] == "}")
            i += 1
        out.append((match.group(1).strip(), css[match.end() : i - 1]))
        rest = rest.replace(css[match.start() : i], "")
    out.append(("", rest))
    return out


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.parent.name or p.stem)
def test_every_grid_declares_its_one_column_form(page: Path) -> None:
    """A grid whose columns exist only inside a breakpoint has an `auto` track below it."""
    css = stylesheet(page)
    base = {selector for selector, _, media in declarations(css) if not media}
    guarded = {
        selector for selector, _, media in declarations(css) if media and "min-width" in media
    }

    # `.duo.flip` reuses `.duo`'s base declaration, so a compound selector counts as covered when
    # its first class is. Anything else must say what it does at phone width itself.
    missing = sorted(
        s
        for s in guarded - base
        if not any(b.split(",")[0].strip() and s.startswith(b.split(":")[0]) for b in base)
    )
    assert not missing, (
        f"{page.name}: these grids only declare columns inside a breakpoint, so below it their "
        "single track is `auto` and grows to the widest unwrapped thing inside — the page then "
        "scrolls sideways on a phone. Add `grid-template-columns: minmax(0, 1fr)` to the base "
        "rule:\n  " + "\n  ".join(missing)
    )


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.parent.name or p.stem)
def test_every_flexible_track_has_a_zero_floor(page: Path) -> None:
    """`1fr` is `minmax(auto, 1fr)`: it will not shrink below its content. `minmax(0, 1fr)` will."""
    offenders = [
        f"{selector} :: {value}" + (f"   @media {media}" if media else "")
        for selector, value, media in declarations(stylesheet(page))
        if BARE_FR.search(BOUNDED.sub("", value))
    ]
    assert not offenders, (
        f"{page.name}: a bare `fr` track cannot shrink below its content's min-content width, so "
        "one long unbreakable string widens the whole page. Write `minmax(0, 1fr)`:\n  "
        + "\n  ".join(offenders)
    )


def test_the_page_script_defines_everything_it_calls() -> None:
    """A `ReferenceError` in the simulator empties the hero and leaves the rest of the page intact.

    `paint()` calls `css('--accent')`. The helper that defines `css` was deleted in the commit that
    moved the verdict colour onto a class — it looked unused, because after that change the only
    remaining call sites were inside `paint`, and I checked for uses of the *colour* rather than
    uses of the *helper*. Every render then threw, the dot field never drew, and the page still
    looked finished: the number and the verdict are written **before** `paint()` runs, so what
    shipped was a large empty rectangle where the product's one visual argument should be.

    Nothing here could see it. The suite does not execute the page's JavaScript, and the DOM checks
    I ran afterwards read `.sim-out`, which is exactly the part that still worked.

    So: every bare identifier the inline script calls must be defined in it. Deliberately narrow —
    it looks for `name(` and checks that `name` is declared or is a known global. It cannot catch a
    typo inside a string or a property that does not exist. It does catch the whole class of "the
    helper is gone and the call site is not".
    """
    import re as _re

    page = (REPO / "docs" / "index.html").read_text(encoding="utf-8")
    script = "\n".join(_re.findall(r"<script[^>]*>(.*?)</script>", page, _re.S))
    body = _re.sub(r"//[^\n]*", "", _re.sub(r"/\*.*?\*/", "", script, flags=_re.S))

    declared = set(_re.findall(r"\b(?:const|let|var|function)\s+([A-Za-z_$][\w$]*)", body))
    declared |= set(
        _re.findall(r"([A-Za-z_$][\w$]*)\s*(?:=|:)\s*(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", body)
    )
    known = {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "typeof",
        "new",
        "function",
        "requestAnimationFrame",
        "cancelAnimationFrame",
        "setTimeout",
        "clearTimeout",
        "setInterval",
        "clearInterval",
        "matchMedia",
        "getComputedStyle",
        "addEventListener",
        "removeEventListener",
        "parseInt",
        "parseFloat",
        "isNaN",
        "String",
        "Number",
        "Boolean",
        "Array",
        "Object",
        "Math",
        "JSON",
        "Date",
        "Set",
        "Map",
        "RegExp",
        "Error",
        "fetch",
        "encodeURIComponent",
        "decodeURIComponent",
        "IntersectionObserver",
        "MutationObserver",
        "KeyboardEvent",
        "Event",
        "CustomEvent",
        "Promise",
        "queueMicrotask",
        "structuredClone",
    }
    called = {m for m in _re.findall(r"(?<![.\w$])([a-z_$][\w$]*)\s*\(", body)}

    undefined = sorted(called - declared - known)
    assert not undefined, (
        "the inline script calls these and never defines them, so the first call throws and "
        "everything after it in that function silently does not happen:\n  "
        + "\n  ".join(undefined)
    )
