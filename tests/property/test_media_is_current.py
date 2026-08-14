"""Invariant: no image in this repository can show something the product no longer prints.

Every README in the world contains screenshots of a version of the software that may not exist any
more, and the reader has no way to tell. A picture is a claim, and it is the one kind of claim
nobody diffs.

So the images are not pictures of a terminal. Each is a pure function of a file that is already
under review:

    docs/media/<name>.svg  ==  tools/make_media.render(tests/golden/transcripts/<name>.txt)

`tests/golden/test_golden.py` runs each command in a known state and pins the output byte for byte,
so a change to what an operator is told cannot land without somebody looking at it. This file
extends that one property to the pictures: change the wording, and golden fails; update the
transcript, and this fails until `just media` is re-run and the new image committed.

The failure mode it removes is specific and has happened to every project that ships a README: the
prose gets updated, the screenshot does not, and the most prominent thing on the page is the oldest.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MEDIA = REPO / "docs" / "media"


def _load(name: str):  # type: ignore[no-untyped-def]
    """Load a script from `tools/`, which is deliberately not part of the package.

    These are repository tools, not something a user installs, so they have no importable home.
    Loading by path is the honest way to test them rather than moving them into `src/` to make
    testing tidier.
    """
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


make_media = _load("make_media")


def test_there_are_images_to_check() -> None:
    """Guards against an empty SHOTS table turning every test below into a no-op."""
    assert len(make_media.SHOTS) >= 4


@pytest.mark.parametrize("shot", make_media.SHOTS, ids=lambda s: s.transcript)
def test_the_transcript_behind_each_image_exists(shot: object) -> None:
    """A typo in SHOTS would otherwise surface as a confusing failure in the test below."""
    source = make_media.TRANSCRIPTS / f"{shot.transcript}.txt"  # type: ignore[attr-defined]
    assert source.exists(), (
        f"{source.relative_to(REPO)} is missing — docs/media is generated from the golden "
        "transcripts, so an image can only exist for a command whose output is pinned"
    )


@pytest.mark.parametrize(
    "path, svg", sorted(make_media.expected().items()), ids=lambda a: getattr(a, "name", "")
)
def test_the_committed_image_is_what_the_transcript_renders_to(path: Path, svg: str) -> None:
    assert path.exists(), (
        f"{path.relative_to(REPO)} is missing. Run `just media` and commit the result."
    )
    assert path.read_text(encoding="utf-8") == svg, (
        f"{path.relative_to(REPO)} no longer matches "
        f"tests/golden/transcripts/{path.stem}.txt — the picture and the program disagree.\n"
        "Run `just media` and commit the result; the diff is then the review."
    )


def test_rendering_is_deterministic() -> None:
    """Two renders of the same bytes must be the same bytes.

    Without this, `just media` would rewrite the images on every run and the check above would be a
    permanent, meaningless diff — which is how a staleness check gets switched off.
    """
    once = make_media.expected()
    twice = make_media.expected()
    assert once == twice


def test_no_image_is_committed_that_nothing_generates() -> None:
    """The other direction: an orphaned SVG is an unreviewed claim with nothing behind it."""
    known = {p.name for p in make_media.expected()}
    orphans = sorted(p.name for p in MEDIA.glob("*.svg") if p.name not in known)
    assert not orphans, (
        "these images are in docs/media but nothing in tools/make_media.py generates them, so "
        f"nothing can tell whether they are still true: {orphans}"
    )


# ---------------------------------------------------------------------------- the landing page
#
# It embeds the same images, so it can go stale in the same way and for the same reason — with the
# added hazard that it is the first thing a stranger sees and the last thing anybody re-reads.

make_site = _load("make_site")


def test_the_landing_page_is_what_its_source_builds_to() -> None:
    built = make_site.expected()[make_site.PAGE]
    assert make_site.PAGE.exists(), "docs/index.html is missing. Run `just site`."
    assert make_site.PAGE.read_text(encoding="utf-8") == built, (
        "docs/index.html no longer matches site/page.html and the images it inlines.\n"
        "Run `just site` and commit the result; the diff is then the review."
    )


def test_the_landing_page_inlines_the_generated_images() -> None:
    """The property that makes the page's screenshots as trustworthy as the README's.

    If a placeholder were left unsubstituted, or the page started pointing at artwork nothing
    generates, the picture and the program could drift apart again — which is the whole failure this
    file exists to prevent.
    """
    source = make_site.SOURCE.read_text(encoding="utf-8")
    referenced = set(make_site.PLACEHOLDER.findall(source))
    assert referenced, "site/page.html no longer embeds any generated image"

    generated = {s.transcript for s in make_media.SHOTS}
    assert referenced <= generated, (
        f"the page references images nothing generates: {sorted(referenced - generated)}"
    )

    built = make_site.expected()[make_site.PAGE]
    assert "{{MEDIA:" not in built, "a placeholder survived the build"

    # Transcripts used to be the only SVGs on this page, so counting `svg+xml` data URIs and
    # expecting the number of placeholders was the same statement as "every transcript is inlined".
    # The mark broke that equivalence — it is an SVG too, and it appears in the favicon link and in
    # the nav — and a count is the wrong shape of assertion once two kinds of thing are being
    # counted. So: every transcript is present by its own bytes, and every other SVG on the page is
    # the mark. That stays true however many times either is used.
    import base64

    for name in sorted(referenced):
        raw = (make_media.MEDIA / f"{name}.svg").read_bytes()
        uri = "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")
        assert uri in built, f"{name}.svg is referenced by the page but not inlined into it"

    mark = base64.b64encode((make_site.LOGO / "mark.svg").read_bytes()).decode("ascii")
    others = built.count("data:image/svg+xml;base64,") - len(referenced)
    assert others == built.count(mark), (
        "the page inlines an SVG that is neither a generated transcript nor the mark, so something "
        "is on the front door that nothing here can tell the truth of"
    )


def test_every_console_screenshot_exists_and_is_documented() -> None:
    """The images that carry a weaker guarantee, held to the one guarantee they can carry.

    `{{CONSOLE:…}}` images are screenshots of a running console, taken by hand. Nothing can tell
    automatically when they stop being true — which is exactly why the file recording how and when
    each was taken has to exist, and has to name every one of them. An undocumented screenshot on a
    landing page is a claim with no provenance at all.
    """
    source = make_site.SOURCE.read_text(encoding="utf-8")
    referenced = set(make_site.CONSOLE_PLACEHOLDER.findall(source))
    assert referenced, "the page no longer shows the console"

    missing = sorted(n for n in referenced if not (make_site.CONSOLE / f"{n}.png").exists())
    assert not missing, f"referenced but not in docs/media/console: {missing}"

    provenance = make_site.CONSOLE / "PROVENANCE.md"
    assert provenance.exists(), "docs/media/console/PROVENANCE.md is missing"
    text = provenance.read_text(encoding="utf-8")
    undocumented = sorted(n for n in referenced if f"{n}.png" not in text)
    assert not undocumented, (
        "these screenshots are on the landing page but PROVENANCE.md does not say how they were "
        f"taken, so nobody can re-take them or tell when they went stale: {undocumented}"
    )


def test_no_console_screenshot_is_committed_that_nothing_shows() -> None:
    source = make_site.SOURCE.read_text(encoding="utf-8")
    referenced = set(make_site.CONSOLE_PLACEHOLDER.findall(source))
    orphans = sorted(p.name for p in make_site.CONSOLE.glob("*.png") if p.stem not in referenced)
    assert not orphans, f"in docs/media/console but shown nowhere: {orphans}"


# ---------------------------------------------------------------------------- the PyPI README
#
# The package page is where most people meet this project, and PyPI renders the long description
# with no base URL — so every relative path in README.md is a broken image or a dead link there.

make_readme = _load("make_readme_pypi")


def test_the_pypi_readme_is_what_the_readme_generates() -> None:
    assert make_readme.TARGET.exists(), "README.pypi.md is missing. Run `just readme-pypi`."
    assert make_readme.TARGET.read_text(encoding="utf-8") == make_readme.expected(), (
        "README.pypi.md no longer matches README.md.\nRun `just readme-pypi` and commit the result."
    )


def test_the_pypi_readme_has_no_relative_links_left() -> None:
    """The whole reason it exists, asserted rather than assumed.

    A rewrite rule that silently stopped matching would leave the generated file looking fine in a
    checkout and broken on the package page, which is the one place nobody here looks.
    """
    import re

    text = make_readme.expected()
    relative = re.findall(r'src="(?!https?://)[^"]+"', text) + re.findall(
        r"\]\((?!https?://|#)[^)]+\)", text
    )
    assert not relative, f"these would be dead on PyPI: {relative[:6]}"


def test_the_packaged_readme_is_the_generated_one() -> None:
    """`pyproject.toml` has to point at it, or none of the above matters."""
    import tomllib

    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["readme"] == "README.pypi.md"


# ---------------------------------------------------------------------------- the runtime matrix
#
# A compatibility table is the single most quoted thing in a README and the least diffed. This one
# is generated from `eval/results/conformance.json`, so the same rule that holds for the images
# holds for it: edit it by hand and the build fails.

make_matrix = _load("make_matrix")


def test_the_conformance_table_is_what_the_recorded_run_produces() -> None:
    assert make_matrix.README.read_text(encoding="utf-8") == make_matrix.expected(), (
        "README.md's runtime table no longer matches eval/results/conformance.json.\n"
        "Run `just conformance` then `just matrix`, and commit both."
    )


def test_every_version_in_the_table_is_the_version_installed_here() -> None:
    """The claim the table exists to make, checked against reality rather than against itself.

    "tested with LangChain 1.3.14" is only worth reading while 1.3.14 is what the suite runs on.
    Upgrade the framework, and the recorded evidence quietly becomes a statement about a version
    nobody exercised — which is exactly the failure a version number is supposed to prevent.

    Absent versions are skipped rather than failed: a framework that is not installed here was
    recorded as `skipped`, and the table already says so.
    """
    import json
    from importlib.metadata import PackageNotFoundError, version

    from tests.conformance.conftest import DISTRIBUTION

    if not make_matrix.RESULTS.exists():
        pytest.skip("nobody has run `just conformance` here")

    rows = json.loads(make_matrix.RESULTS.read_text(encoding="utf-8"))["runtimes"]
    drifted = []
    for name, row in sorted(rows.items()):
        recorded = row.get("version") or ""
        if not recorded:
            continue
        try:
            installed = version(DISTRIBUTION.get(name, name))
        except PackageNotFoundError:
            continue
        if installed != recorded:
            drifted.append(f"{name}: table says {recorded}, installed is {installed}")
    assert not drifted, (
        "the runtime table is making claims about versions that are not the ones here:\n  "
        + "\n  ".join(drifted)
        + "\nRun `just conformance` then `just matrix`."
    )


# ---------------------------------------------------------------------------- the mark
#
# The favicon and the logo are generated from one set of constants for the same reason the images
# are generated from transcripts: a mark redrawn by hand wherever it is needed is several marks that
# agree until the first time one of them is edited. Both pages inline it, so a stale file here ships
# on the front door.

make_logo = _load("make_logo")


def test_the_mark_is_what_its_geometry_builds_to() -> None:
    """Compared as images, which is the only form of this that holds on more than one machine.

    Written first as a byte comparison, and it was red in CI for three commits: PNG and ICO are
    encoder output, and neither Pillow nor zlib promises the same bytes across versions or
    platforms. The files were generated on macOS and CI runs Linux. The mark is the pixels.
    """
    stale = [
        str(path.relative_to(make_logo.REPO))
        for path, data in make_logo.outputs().items()
        if not make_logo.matches(path, data)
    ]
    assert not stale, (
        "these no longer match the geometry in tools/make_logo.py. Run `just logo`:\n  "
        + "\n  ".join(sorted(stale))
    )


def test_the_staleness_check_is_not_comparing_bytes() -> None:
    """The bug that made the check above fail everywhere except the machine that wrote the files.

    Re-encoding a PNG at a different compression level changes every byte and no pixel. If
    `matches` ever goes back to comparing bytes, this is the difference between a check that
    travels and one that only agrees with its author's laptop.
    """
    from io import BytesIO

    from PIL import Image

    path = make_logo.OUT / f"icon-{make_logo.TOUCH}.png"
    reencoded = BytesIO()
    Image.open(path).save(reencoded, format="PNG", compress_level=1)

    assert reencoded.getvalue() != path.read_bytes(), "expected different bytes to compare against"
    assert make_logo.matches(path, reencoded.getvalue()), (
        "matches() rejected a re-encoding of the very same image, so it is comparing bytes rather "
        "than pixels and will fail on any machine but the one that generated the files"
    )


def test_every_icon_size_is_drawn_rather_than_resampled() -> None:
    """The slot is one pixel wide at 16px, which is the size that decides a favicon.

    Handing Pillow a single image and a list of `sizes` makes it resample that one image down to
    each entry, and the first version of this did exactly that — the 16px frame came back
    byte-identical to a LANCZOS downscale of the 48. A one-pixel cut does not survive that; it
    becomes a grey smear across a blue square, which is a different mark.
    """
    from io import BytesIO

    from PIL import Image

    icon = Image.open(BytesIO((make_logo.OUT / "favicon.ico").read_bytes()))
    assert sorted(icon.ico.sizes()) == sorted((n, n) for n in make_logo.ICO_SIZES), (
        f"the icon carries {sorted(icon.ico.sizes())}, not every size make_logo declares"
    )

    for size in make_logo.ICO_SIZES:
        icon.size = (size, size)
        drawn = make_logo.raster(size)
        assert icon.convert("RGBA").tobytes() == drawn.tobytes(), (
            f"the {size}px frame is not the mark drawn at {size}px — it has been resampled from "
            "another size, and the cut is a single pixel at the smallest one"
        )


def test_the_head_delivers_the_card_it_promises() -> None:
    """`twitter:card: summary_large_image` is a promise, and it went years without one.

    The head declared a large-image card and named no image at all, so a shared link expanded to
    bare text or to whatever the platform could scrape. Nobody sees their own link previews, which
    is exactly why this is the kind of claim that needs a test rather than a reader.

    The card cannot be a `data:` URI like every other image here — a scraper fetches `og:image` by
    URL from its own machine and never loads the page — so this also checks it is reachable by the
    address the head gives out.
    """
    from neti._website import WEBSITE

    built = make_site.expected()
    for page, path in ((make_site.PAGE, ""), (make_site.CLOUD_PAGE, "cloud")):
        html = built[page]
        assert 'name="twitter:card" content="summary_large_image"' in html
        assert f'property="og:image" content="{WEBSITE}card.png"' in html, (
            f"{page.name} promises a large-image card and names no image"
        )
        assert f'property="og:url" content="{WEBSITE}{path}"' in html, (
            f"{page.name} names a canonical URL that is not its own, which tells a scraper this "
            "page and another are the same document"
        )

    served = make_site.REPO / "public" / "card.png"
    assert served.exists(), (
        "public/card.png is missing, so og:image points at a 404. Run `just card`."
    )


# ---------------------------------------------------------------------------- the cast


COVERED_BY_THE_CAST_FACE = {"·", "—", "─"}
"""Every non-ASCII character `jetbrains-mono-regular.woff2` is known to draw.

Verified against the committed font's cmap when it was chosen — U+00B7, U+2014, U+2500 — and it is
that verification this set records. The point is not the three characters; it is that adding a
fourth to the transcript has to be a deliberate act with somebody checking the font, rather than
258 empty boxes nobody notices.
"""


def test_the_cast_draws_no_character_its_face_lacks() -> None:
    """The defect that shipped once, checked without an optional dependency.

    The cast was first built in Space Mono, whose Google subset has no U+2500 — the box-drawing
    horizontal ruling every act heading, 258 times. PIL drew 258 empty boxes and reported nothing.

    The first version of this test read the font's cmap directly, which needs fontTools to
    decompress a woff2, which CI does not install. I guarded it with `importorskip` — and
    `test_no_silent_skips.py` failed the build for it, correctly: this repository already decided
    that a skip reads exactly like a pass, having lost eighteen adapter tests to one. Writing "a
    skip reads exactly like a pass" in the docstring while adding a skip was not a good look.

    So this compares the transcript against what was verified rather than re-deriving it. It needs
    nothing but the transcript, runs everywhere, and fails the moment the cast would contain a
    character nobody has checked the face can draw. The stronger check still exists where it can
    afford its dependency: `frames()` calls `_assert_covered` before drawing, so `just cast` cannot
    emit a cast with a missing glyph whatever this file does.
    """
    make_cast = _load("make_cast")

    lines = make_cast._lines()
    assert lines, "the cast has no lines, so this checks nothing"

    exotic = {ch for line, _ in lines for ch in line if ord(ch) > 126}
    assert exotic, (
        "the transcript is pure ASCII now, so this test proves nothing — it was written because "
        "the act rules are U+2500. Check whether the cast still looks like the exhibit."
    )
    unverified = exotic - COVERED_BY_THE_CAST_FACE
    assert not unverified, (
        "the cast would draw "
        + ", ".join(f"U+{ord(ch):04X} {ch!r}" for ch in sorted(unverified))
        + ", which nobody has confirmed the face contains. Check it against "
        f"{make_cast.FACE.name} and add it above, or the cast gets empty boxes and says nothing."
    )

    assert make_cast.OUT.exists(), "docs/media/cast.gif is missing. Run `just cast`."
