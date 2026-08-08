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


def test_prove_runs_on_the_policy_neti_start_writes(tmp_path: Path) -> None:
    """The command the first run points at has to work on the file the first run wrote.

    `prove` drove one fixed Entra call, so the default config was the one config it refused. After
    `neti start` — and `start`, the demo transcripts and the README all name it — the command
    printed a tidy error suggesting `neti prove`, which was the command that had just failed.

    It now picks a call the policy in hand will stop. Asserted on the *output*, because a proof
    that ran and disagreed with itself exits non-zero and would otherwise read as a pass.
    """
    from typer.testing import CliRunner

    from neti.cli import _packaged_example, app
    from neti.insight.edit_policy import apply_preset, plan_preset

    coding = _packaged_example("coding-agent.yaml")
    assert coding is not None
    policy = tmp_path / "neti.yaml"
    policy.write_text(coding.read_text(encoding="utf-8"), encoding="utf-8")
    apply_preset(
        plan_preset(
            policy,
            bands=[{"above": 500, "verdict": "flag"}],
            rules=[],
            outside_root="confirm",
        )
    )

    result = CliRunner().invoke(app, ["prove", "-c", str(policy), "-r", str(tmp_path / "p.ndjson")])

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "outside the declared root" in result.output, "it has to say why this call was stopped"
    assert "AND THEY DISAGREE" not in result.output


def test_prove_explains_itself_rather_than_raising_when_nothing_can_be_driven(
    tmp_path: Path,
) -> None:
    """A policy with no stopping rule cannot answer the question, and must not look like a crash.

    Every seam driver asserts the call did not reach the tool, so a policy that only flags ends in
    `AssertionError: the gate let the call through` under a rich traceback — which reads as a broken
    product rather than as a policy that has not declared anything yet.
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
    assert "can be driven" in result.output
    assert "neti start" in result.output, "it has to say what to run instead"


# ---------------------------------------------------------------------------- the empty directory
#
# What a stranger meets. Every one of these was reached by installing the package into a clean
# virtualenv and running the documented flow, and every one of them used to answer with a raw
# `[Errno 2] No such file or directory` or, for `neti demo`, a FileNotFoundError traceback. The
# difference between a tool that is missing a file and a tool that is broken is entirely in this
# message, and nothing in a source checkout can see it: the files are simply there.

# Every command that reads a policy or a records file, which is every command that can meet an
# empty directory. The first version of this listed six and missed `console`, `serve` and `gate` —
# so those three still answered a raw errno after the other seven had been fixed, and a shipping
# check on the built wheel is what found them. Derived from the app's own option list below rather
# than typed out again, so a new command joins this automatically.
FIRST_RUN = [
    ("inventory", []),
    ("report", []),
    ("propose", []),
    ("verify", []),
    ("score", []),
    ("install", []),
    ("console", ["--no-open"]),
    ("gate", ["--stdio", "--", "echo", "hi"]),
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


def test_status_answers_in_an_empty_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every other command here refuses; this one is supposed to answer.

    Somebody running `neti status` in a directory with no policy is asking exactly the question it
    exists for, and the honest answer is "nothing is protecting this, run `neti start`" — not a
    usage error. Exit 1 rather than 2, because the code means *not protected* and that is a thing a
    prompt or a CI step needs to branch on.
    """
    from typer.testing import CliRunner

    from neti.cli import app

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    assert "NOT protecting" in result.output
    assert "neti start" in result.output


def test_no_command_that_reads_a_policy_was_left_out_of_the_check_above() -> None:
    """The list is only worth something if it is the whole list.

    `console`, `serve` and `gate` all took `--config`, all answered a raw errno, and none of them
    was in `FIRST_RUN` — so seven commands got fixed and three did not, and nothing failed. Derived
    here from the app's own options so the omission cannot happen silently again.

    Three are exempt, each for a stated reason and each covered elsewhere:

      hook    a `PreToolUse` hook exiting non-zero fails the tool call it was asked about, so it
              reports and exits 0. `tests/e2e/test_never_breaks_the_agent.py` holds that line.
      serve   its default is a shipped example, so an empty directory is not an error.
      demo    same, and `test_demo_runs_with_no_policy_and_no_arguments` above asserts it.
      prove   same, and `test_prove_explains_itself_rather_than_raising_on_the_wrong_policy`
              covers the case where the policy exists but cannot answer its question.
      start   an empty directory is the case it is FOR — it writes the policy rather than refusing,
              so the exit-2 assertion below is the wrong shape for it entirely.
              `tests/e2e/test_first_run_orients.py` drives it in an empty directory and checks it
              measures something, blocks nothing, and says what to do next.
      status  its entire job is to answer in an empty directory rather than refuse — "there is no
              policy here, run `neti start`" IS the report. It exits 1, not 2, because the exit code
              means *not protected* and a script has to be able to act on that distinction.
              `test_status_answers_in_an_empty_directory` below asserts it.

    An exemption is a claim, so each one names where the behaviour is actually checked. A list of
    exemptions with no tests behind them would be this check quietly switching itself off.
    """
    from neti.cli import app

    exempt = {"hook", "serve", "demo", "prove", "start", "status"}
    takes_policy = {
        command.callback.__name__.replace("_", "-")
        for command in app.registered_commands
        if command.callback and "config" in command.callback.__code__.co_varnames
    }
    checked = {name for name, _ in FIRST_RUN} | exempt
    missing = sorted(takes_policy - checked)
    assert not missing, (
        f"these commands read a policy and are not checked against an empty directory: {missing}"
    )


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
