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


def _make_media():  # type: ignore[no-untyped-def]
    """Load `tools/make_media.py`, which is deliberately not part of the package.

    It is a repository tool, not something a user installs, so it has no importable home. Loading it
    by path is the honest way to test it rather than moving it into `src/` to make testing tidier.
    """
    spec = importlib.util.spec_from_file_location("make_media", REPO / "tools" / "make_media.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_media"] = module
    spec.loader.exec_module(module)
    return module


make_media = _make_media()


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
