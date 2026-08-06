"""`DESIGN.md`, enforced. A design rule nobody checks is a comment.

Three things drifted apart in this repository before anything was written down: the console
re-skinned itself to violet while the landing page kept Claritty's indigo, so neti had two
identities and one of them was somebody else's; the console filled up with card wrappers the
website never had; and every "nothing here yet" moment got hand-rolled a different way.

None of that was disagreement. There was nothing to disagree with. `DESIGN.md` is now the
statement, and this file is what makes it fail the build.

**The palette section replaces a lie.** `globals.css` opened with *"The accent was CHOSEN BY
RUNNING THE PALETTE VALIDATOR, not by taste"* and cited cyan failing at ΔE 12.5 against the reserved
emerald. No validator existed anywhere in the repository, and that number does not reproduce — cyan
measures 28.8 under CIEDE2000. The claim may have rested on some other metric; what is certain is
that nobody could check it. So the measurement lives here, recomputed on every run, and the comment
points at the test rather than the other way round.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GLOBALS = REPO / "web" / "src" / "app" / "globals.css"
SITE = REPO / "site" / "page.html"
WEB_SRC = REPO / "web" / "src"
BUILT_CSS = REPO / "src" / "neti" / "console" / "_next" / "static" / "css"

ACCENT = "#4568F3"
"""neti's primary: Fireblocks' brand accent, read off their own site rather than eyeballed."""

RESERVED = {"block": "#EF4444", "confirm": "#F59E0B", "allow": "#10B981"}

DELTA_E_FLOOR = 15.0
"""Below this, two marks are not reliably tellable apart at a glance. The number the old header
claimed to be enforcing, now actually enforced."""


# --------------------------------------------------------------------------- colour maths


def _lab(value: str) -> tuple[float, float, float]:
    text = value.lstrip("#")
    r, g, b = (int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))
    lin = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    r, g, b = lin(r), lin(g), lin(b)
    x = r * 0.4124564 + g * 0.3575761 + b * 0.1804375
    y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
    z = r * 0.0193339 + g * 0.1191920 + b * 0.9503041
    f = lambda t: t ** (1 / 3) if t > 216 / 24389 else (841 / 108) * t + 4 / 29  # noqa: E731
    fx, fy, fz = f(x / 0.95047), f(y), f(z / 1.08883)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e(one: str, two: str) -> float:
    """CIEDE2000. Perceptual difference, which is the only kind that matters to a reader."""
    l1, a1, b1 = _lab(one)
    l2, a2, b2 = _lab(two)
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    cb = (c1 + c2) / 2
    g = 0.5 * (1 - math.sqrt(cb**7 / (cb**7 + 25**7))) if cb > 0 else 0.0
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1 = math.degrees(math.atan2(b1, a1p)) % 360
    h2 = math.degrees(math.atan2(b2, a2p)) % 360
    dl, dc = l2 - l1, c2p - c1p
    if c1p * c2p == 0:
        dh = 0.0
    elif h2 - h1 > 180:
        dh = h2 - h1 - 360
    elif h2 - h1 < -180:
        dh = h2 - h1 + 360
    else:
        dh = h2 - h1
    dhp = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh) / 2)
    lb, cbp = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        hb = h1 + h2
    elif abs(h1 - h2) > 180:
        hb = (h1 + h2 + 360) / 2 if h1 + h2 < 360 else (h1 + h2 - 360) / 2
    else:
        hb = (h1 + h2) / 2
    t = (
        1
        - 0.17 * math.cos(math.radians(hb - 30))
        + 0.24 * math.cos(math.radians(2 * hb))
        + 0.32 * math.cos(math.radians(3 * hb + 6))
        - 0.20 * math.cos(math.radians(4 * hb - 63))
    )
    sl = 1 + (0.015 * (lb - 50) ** 2) / math.sqrt(20 + (lb - 50) ** 2)
    sc, sh = 1 + 0.045 * cbp, 1 + 0.015 * cbp * t
    rt = (
        -2
        * math.sqrt(cbp**7 / (cbp**7 + 25**7))
        * math.sin(math.radians(60 * math.exp(-(((hb - 275) / 25) ** 2))))
    )
    return math.sqrt(
        (dl / sl) ** 2 + (dc / sc) ** 2 + (dhp / sh) ** 2 + rt * (dc / sc) * (dhp / sh)
    )


# --------------------------------------------------------------------------- the palette


@pytest.mark.parametrize("name, colour", sorted(RESERVED.items()))
def test_the_accent_is_tellable_apart_from_every_reserved_verdict(name: str, colour: str) -> None:
    """An accent a reader could mistake for a verdict is a gate that lies at a glance."""
    measured = delta_e(ACCENT, colour)
    assert measured >= DELTA_E_FLOOR, (
        f"the accent {ACCENT} sits ΔE {measured:.1f} from the reserved {name} colour {colour}, "
        f"under the floor of {DELTA_E_FLOOR}. Somebody could not reliably tell an accent mark from "
        "a verdict mark."
    )


def test_the_three_verdicts_are_tellable_apart_from_each_other() -> None:
    """The scale is the product's vocabulary. If two of its words look alike it has two words."""
    names = sorted(RESERVED)
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            measured = delta_e(RESERVED[first], RESERVED[second])
            assert measured >= DELTA_E_FLOOR, (
                f"{first} and {second} are ΔE {measured:.1f} apart, under {DELTA_E_FLOOR}"
            )


def test_the_console_and_the_site_agree_on_the_accent() -> None:
    """The exact drift that made `DESIGN.md` necessary.

    The console re-skinned to violet and the landing page did not, so neti had two identities and
    one of them was Claritty's. This is the assertion that stops it happening quietly again.
    """
    css = GLOBALS.read_text(encoding="utf-8")
    hsl = set(re.findall(r"--accent:\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%", css))
    assert hsl, "no --accent in globals.css"
    assert len(hsl) == 1, f"the console declares more than one accent: {hsl}"

    site = set(re.findall(r"--accent:\s*(#[0-9A-Fa-f]{6})", SITE.read_text(encoding="utf-8")))
    assert site == {ACCENT}, f"site/page.html declares {site or 'no accent'}, expected {ACCENT}"

    hue, sat, light = (float(v) for v in next(iter(hsl)))
    expected = tuple(round(v, 1) for v in _hsl_of(ACCENT))
    assert (round(hue), round(sat), round(light)) == tuple(round(v) for v in expected), (
        f"the console's accent hsl({hue} {sat}% {light}%) is not {ACCENT} — "
        "the two surfaces have drifted apart again"
    )


def _hsl_of(value: str) -> tuple[float, float, float]:
    text = value.lstrip("#")
    r, g, b = (int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))
    high, low = max(r, g, b), min(r, g, b)
    light = (high + low) / 2
    if high == low:
        return (0.0, 0.0, light * 100)
    delta = high - low
    sat = delta / (2 - high - low) if light > 0.5 else delta / (high + low)
    if high == r:
        hue = ((g - b) / delta) % 6
    elif high == g:
        hue = (b - r) / delta + 2
    else:
        hue = (r - g) / delta + 4
    return (hue * 60, sat * 100, light * 100)


def test_the_built_console_carries_the_accent() -> None:
    """`web/` is a Next.js app whose build output is committed. A token change nobody rebuilt is a
    change nobody ships, and the source would look right the whole time."""
    if not BUILT_CSS.exists():
        pytest.skip("no built console in this checkout")
    built = " ".join(p.read_text(encoding="utf-8") for p in BUILT_CSS.glob("*.css"))
    hue, sat, light = _hsl_of(ACCENT)
    needle = f"{hue:.0f} {sat:.0f}% {light:.0f}%"
    assert needle in built.replace("  ", " "), (
        f"the built console CSS does not carry the accent ({needle}). "
        "Run `npm run build` in web/ and commit src/neti/console/."
    )


# --------------------------------------------------------------------------- structure


def _sources() -> list[Path]:
    return sorted(p for p in WEB_SRC.rglob("*.tsx") if "node_modules" not in p.parts)


def test_no_component_hardcodes_a_colour() -> None:
    """A hex in a component is a light-mode-only bug waiting, and it is how the drift started."""
    offenders = []
    for path in _sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"#[0-9A-Fa-f]{6}\b", line) and "currentColor" not in line:
                offenders.append(f"{path.relative_to(REPO)}:{number}: {line.strip()[:80]}")
    assert not offenders, (
        "hardcoded colours — use a token (bg-accent, text-muted-foreground, border-border):\n  "
        + "\n  ".join(offenders[:10])
    )


def test_nothing_reaches_for_a_card() -> None:
    """DESIGN.md: a number does not need a box.

    The console's overview was three bordered rounded rectangles stacked on a bordered table — four
    boxes to say three numbers. A border earns its place only when the thing inside is genuinely
    detachable from the page.
    """
    offenders = [
        str(path.relative_to(REPO))
        for path in _sources()
        if re.search(
            r"from \"[~.@/]*components/ui/card\"|<Card[ >]", path.read_text(encoding="utf-8")
        )
    ]
    assert not offenders, (
        "these import or render a Card. Structure comes from hairline rules and spacing:\n  "
        + "\n  ".join(offenders)
    )


def test_the_stylesheet_itself_obeys_the_no_shadow_rule() -> None:
    """The gap that let liquid glass survive the rule that forbids it.

    `test_nothing_casts_a_shadow` greps components for `shadow-*` utilities. It never looked at the
    stylesheet, so `.glass-card` — backdrop blur, saturation, a translucent fill, an inset ring, and
    an `-elevated` variant carrying two drop shadows — sat in `globals.css` applying all of it to
    twenty-one elements while every component-level check passed.

    Liquid glass is the *Claritty platform's* default surface. It is not neti's, and DESIGN.md says
    so. A rule that only checks the places somebody remembered to look is not a rule.
    """
    # Comments stripped properly rather than line-by-line: the prose explaining *why* liquid glass
    # was removed says "backdrop blur", and a check that cannot tell a declaration from a sentence
    # about a declaration fails on its own documentation.
    body = re.sub(r"/\*.*?\*/", "", GLOBALS.read_text(encoding="utf-8"), flags=re.S)
    for banned in ("backdrop-blur", "box-shadow", "backdrop-saturate"):
        assert banned not in body, (
            f"`{banned}` is declared in globals.css. Depth is not this product's idea (DESIGN.md), "
            "and a utility class is exactly where that rule stops being enforced."
        )


def test_nothing_draws_a_dashed_box() -> None:
    """DESIGN.md, verbatim: an empty state is open space with something living in it, not a plate.

    `/gate` shipped a dashed rounded rectangle for months and it is named in the anti-patterns
    table, so it gets an assertion rather than a promise.
    """
    offenders = []
    for path in _sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "border-dashed" in line:
                offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert not offenders, (
        "dashed boxes — use EmptyState, or a section with a rule:\n  " + "\n  ".join(offenders)
    )


def test_everything_you_press_is_a_pill() -> None:
    """DESIGN.md: `rounded-full` is reserved for the things you press.

    Reserving one shape for "interactive" only works while it is actually reserved — a single
    `rounded-lg` button undoes the distinction for every pill on the page, because the reader can no
    longer tell shape from decoration.

    Inputs are exempt and deliberately so: a pill-shaped textarea reads as a chat box.
    """
    offenders = []
    for path in _sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            interactive = "bg-accent px-" in line or "glass-button" in line
            if interactive and re.search(r"rounded-(lg|md|xl|2xl|\[)", line):
                offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert not offenders, "these look pressable but are not pills (DESIGN.md):\n  " + "\n  ".join(
        offenders[:10]
    )


def test_nothing_that_is_not_a_control_wears_a_pill() -> None:
    """The assertion that was missing, and the reason a bad build shipped.

    `test_everything_you_press_is_a_pill` checks that pressable things *are* pills. It never checked
    the inverse, so when a blanket regex turned every `rounded-*` into `rounded-full` the suite
    stayed green while the console rendered: a 1,500px stadium wrapped around the gate's empty
    state, a pill-shaped three-line `<pre>`, and a decision log of fourteen chat bubbles.

    A shape that means "press me" only means it while nothing else wears it. So a line carrying
    `rounded-full` has to carry a control signal too — a button, a click handler, an accent fill, a
    chip's padding, or one of the small fixed-size dots and rings that are round by nature.
    """
    signals = (
        "<button",
        "onClick",
        "bg-accent px-",
        "px-2 py-0.5",
        "px-2.5 py-0.5",
        "px-3 py-1.5",
        "place-items-center",
        "min-h-11 items-center",
        "min-h-9 items-center",
        "blur-3xl",
        "p-1.5",
        "rounded-full bg-accent",
        "rounded-full bg-border",
        "rounded-full bg-muted",
        "h-1.5",
        "h-2 w-2",
        "h-2.5 w-2.5",
        "h-3 w-3",
        "h-11 w-11",
        # A 2px-tall proportional bar has rounded ends because it is a bar, the same way the status
        # dots above are circles. Caught by this very rule the day it was written, which is the
        # rule working: the exemption is now stated rather than assumed.
        "h-2 w-full",
        # A segmented control's rail. It holds the segments and is pressed through, so it is a
        # control and takes the control's shape — a pill segment floating inside a rectangle was
        # what the de-pilling sweep left behind, and it looked exactly as odd as it sounds.
        "p-0.5",
    )
    offenders = []
    for path in _sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "rounded-full" in line and not any(s in line for s in signals):
                offenders.append(f"{path.relative_to(REPO)}:{number}: {line.strip()[:70]}")
    assert not offenders, (
        "these wear a control's shape without being controls — a container, a row or a code block "
        "with `rounded-full` (DESIGN.md):\n  " + "\n  ".join(offenders[:10])
    )


def test_nothing_casts_a_shadow() -> None:
    """DESIGN.md: depth is not this product's idea."""
    offenders = []
    for path in _sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bshadow-(sm|md|lg|xl|2xl|inner|\[)", line):
                offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert not offenders, "no shadows:\n  " + "\n  ".join(offenders[:10])


def test_nothing_moves_on_hover() -> None:
    """DESIGN.md: a background or border transition is enough; tap-scale only."""
    offenders = []
    for path in _sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"hover:(scale|translate|rotate)", line):
                offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert not offenders, "no hover motion:\n  " + "\n  ".join(offenders[:10])


def test_design_md_exists_and_names_the_accent() -> None:
    """The rules have to be somewhere a reviewer can cite, or they are folklore."""
    design = (REPO / "DESIGN.md").read_text(encoding="utf-8")
    assert ACCENT in design
    for colour in RESERVED.values():
        assert colour in design, f"{colour} is reserved but DESIGN.md does not say so"


# ---------------------------------------------------------------------------- motion


SCENES = sorted((REPO / "web" / "src" / "components" / "live" / "scenes").glob("*.tsx"))


def test_there_are_scenes_to_check() -> None:
    """Guards the two tests below against silently becoming no-ops."""
    assert len(SCENES) >= 4, "the live scenes have moved or vanished"


@pytest.mark.parametrize("scene", SCENES, ids=lambda p: p.stem)
def test_every_scene_composes_a_frame_when_motion_is_off(scene: Path) -> None:
    """DESIGN.md: reduced motion still renders a composed final frame, never a blank box.

    A CSS `prefers-reduced-motion` rule cannot stop a JS timer, so the kernel gates the loop and
    each scene has to choose what to show when the loop is not running. A scene that simply renders
    its initial state disappears for exactly the people who asked for less movement, which is not
    accessible — it is absent.

    Checked structurally rather than by screenshot: the scene must branch on `live`, so the frozen
    state is a deliberate choice somebody made rather than whatever index a counter happened to
    start at.
    """
    body = scene.read_text(encoding="utf-8")
    assert "useLiveGate" in body or "useSceneRoot" in body, (
        f"{scene.name} does not gate its motion at all"
    )
    assert re.search(r"live\s*\?", body), (
        f"{scene.name} never branches on `live`, so with motion off it renders whatever its "
        "counter started at — usually an empty stage. Pick the frozen frame deliberately."
    )


@pytest.mark.parametrize("scene", SCENES, ids=lambda p: p.stem)
def test_no_scene_reaches_for_a_frame_loop_or_the_network(scene: Path) -> None:
    """Timers only. A scene must be safe in a cold, offline launch."""
    body = scene.read_text(encoding="utf-8")
    for banned in ("requestAnimationFrame", "getContext(", "fetch(", "new Image("):
        assert banned not in body, f"{scene.name} uses {banned}"
