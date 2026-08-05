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
    assert built.count("data:image/svg+xml;base64,") == len(referenced)


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
