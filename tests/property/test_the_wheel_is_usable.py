"""Invariant: what gets installed is enough to run the command the README opens with.

Everything else in this suite runs from a source checkout, where the repository root is two
directories up from `src/neti/cli.py` and every example, fixture and document is simply *there*.
That is not the layout a customer has. In `site-packages/neti/`, two directories up is
`lib/python3.12/`, and anything the wheel did not carry is gone.

`neti demo --here` is the first command the README gives a stranger. It defaults to
`examples/coding-agent.yaml`, that file was only ever resolved relative to a checkout, and so on a
real install the headline command answered:

    error: cannot find examples/coding-agent.yaml. Pass -c with your own policy.

Found by building the package, installing it into an empty virtualenv, and running the documented
first command — which is the only way this class of defect ever surfaces, and is why it survived a
suite of nearly two thousand tests.

These tests read the built artifact rather than the working tree, so they fail for the same reason a
customer would.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Every example the CLI can reach for by name. `coding-agent.yaml` is `demo --here`'s default and
# `entra.yaml` is the default for `inventory`, `report`, `propose` and `prove`.
REQUIRED_EXAMPLES = ("coding-agent.yaml", "entra.yaml")


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> zipfile.ZipFile:
    """Build the wheel once and read it. No install, so this stays fast enough for every run."""
    out = tmp_path_factory.mktemp("wheel")
    built = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out), str(REPO)],
        capture_output=True,
        text=True,
    )
    if built.returncode != 0:
        pytest.skip(f"`python -m build` unavailable: {built.stderr.strip().splitlines()[-1:]}")
    wheels = sorted(out.glob("neti-*.whl"))
    assert wheels, f"no wheel produced:\n{built.stdout}"
    return zipfile.ZipFile(wheels[-1])


@pytest.mark.parametrize("name", REQUIRED_EXAMPLES)
def test_the_wheel_carries_the_example_policies(wheel: zipfile.ZipFile, name: str) -> None:
    """The CLI names these by default, so they have to be inside what people install."""
    assert f"neti/examples/{name}" in wheel.namelist(), (
        f"examples/{name} is not in the wheel, so any command that defaults to it fails on a real "
        "install. See [tool.hatch.build.targets.wheel.force-include] in pyproject.toml."
    )


def test_a_packaged_example_is_found_from_the_package_and_not_the_checkout() -> None:
    """`_packaged_example` must look beside the module before it looks at the repository.

    Asserted as an ordering property rather than by installing, so it runs in the normal suite. The
    installed layout has no repository root to fall back to, so if the packaged path is not tried
    first it is never tried at all.
    """
    from neti.cli import _packaged_example

    found = _packaged_example("coding-agent.yaml")
    assert found is not None and found.exists()

    import neti

    packaged = Path(neti.__file__).resolve().parent / "examples" / "coding-agent.yaml"
    if packaged.exists():  # an installed layout, or a checkout that has been built into one
        assert found == packaged, "the packaged copy must win over the checkout's"


def test_a_path_shaped_example_name_still_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`prove`, `inventory`, `report` and `propose` default to the literal "examples/entra.yaml".

    That string was handed straight to `_packaged_example`, which joined it under the package's own
    `examples/` and looked for `neti/examples/examples/entra.yaml`. So `neti prove` with no
    arguments answered "error: no policy at examples/entra.yaml" on every install — including the
    one `neti demo --here` recommends running next, in its closing line.

    **Run from an empty directory, and that is the whole test.** The first version of this passed
    without the fix, because `_packaged_example` falls back to `cwd / name` and the suite runs from
    the repository root, where `examples/entra.yaml` is simply there. It asserted the thing worked
    while measuring a coincidence about the working directory — the same shape of mistake as the
    bug it was written for. From `tmp_path` the fallbacks have nothing to find and only the fix can
    satisfy it.
    """
    from neti.cli import _packaged_example

    monkeypatch.chdir(tmp_path)
    for name in ("entra.yaml", "examples/entra.yaml"):
        found = _packaged_example(name)
        assert found is not None and found.exists(), f"{name!r} did not resolve from an empty cwd"
        assert found.name == "entra.yaml"


def test_prove_explains_itself_rather_than_raising_on_the_wrong_policy(tmp_path: Path) -> None:
    """Pointing `prove` at a policy that cannot answer its question must not look like a crash.

    `prove` drives one fixed call against the synthetic tenant, and every seam driver asserts the
    call was stopped. A coding-agent policy does not gate `remove_group_members`, so the call sailed
    through and the command ended in `AssertionError: the gate let the call through` under a rich
    traceback — which reads as a broken product rather than as the wrong argument.
    """
    from typer.testing import CliRunner

    from neti.cli import _packaged_example, app

    coding = _packaged_example("coding-agent.yaml")
    assert coding is not None
    policy = tmp_path / "neti.yaml"
    policy.write_text(coding.read_text(encoding="utf-8"), encoding="utf-8")

    result = CliRunner().invoke(app, ["prove", "-c", str(policy), "-r", str(tmp_path / "p.ndjson")])

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "does not gate remove_group_members" in result.output
    assert "neti prove" in result.output, "it has to say what to run instead"


# ---------------------------------------------------------------------------- the empty directory
#
# What a stranger meets. Every one of these was reached by installing the package into a clean
# virtualenv and running the documented flow, and every one of them used to answer with a raw
# `[Errno 2] No such file or directory` or, for `neti demo`, a FileNotFoundError traceback. The
# difference between a tool that is missing a file and a tool that is broken is entirely in this
# message, and nothing in a source checkout can see it: the files are simply there.

FIRST_RUN = [
    ("inventory", []),
    ("report", []),
    ("propose", []),
    ("verify", []),
    ("score", []),
    ("install", []),
]


@pytest.mark.parametrize("command, extra", FIRST_RUN, ids=lambda v: v if isinstance(v, str) else "")
def test_a_first_run_in_an_empty_directory_explains_itself(
    command: str, extra: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from neti.cli import app

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, [command, *extra])

    assert result.exit_code == 2, f"expected a clean refusal, got {result.exit_code}"
    assert "Traceback" not in result.output, "a first run must never show a stack trace"
    assert "Errno" not in result.output, "a raw errno is not an explanation"
    assert "neti " in result.output, "it has to name a command to run instead"


def test_demo_runs_with_no_policy_and_no_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`neti demo` defaults to a shipped example and must not need anything from the directory.

    It handed the default string straight to `open()` and ended in a FileNotFoundError traceback on
    every install, which is a poor thing for a command called `demo` to do.
    """
    from typer.testing import CliRunner

    from neti.cli import app

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["demo", "--out", "-"])

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output


def test_every_command_the_guidance_names_actually_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The messages above tell people what to run. Those have to be real commands.

    A dead end that points at another dead end is worse than the original error, and this is the
    check that stops the advice drifting away from the CLI it describes. It is how
    `cp examples/coding-agent.yaml neti.yaml` survived: nothing was comparing the words to the
    program.
    """
    import re

    from typer.testing import CliRunner

    from neti.cli import app

    real = {c.name for c in app.registered_commands if c.name} | {
        c.callback.__name__.replace("_", "-") for c in app.registered_commands if c.callback
    }
    assert "inventory" in real and "prove" in real, f"could not read the command list: {real}"

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    output = "".join(
        runner.invoke(app, [command]).output for command in ("inventory", "report", "install")
    )

    named = set(re.findall(r"\bneti ([a-z][a-z-]+)", output))
    assert named, "the guidance named no commands at all, so this test is checking nothing"

    unknown = sorted(named - real)
    assert not unknown, f"the guidance names commands that do not exist: {unknown}"
