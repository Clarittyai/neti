"""The first ten minutes, asserted — because "I don't understand how to use it" is a bug.

The feedback that produced this file was specific and fair: nineteen commands in one flat list with
`measure` (an internal Graph-latency benchmark) second, no "start here", and a policy format that
asks for a ceiling — a number nobody has on day one. The value moment, `neti demo --here`, was last
in the list under a description that means nothing to a newcomer.

None of that is caught by a test that runs a command and checks it exits 0. So these assert the
shape of the first encounter instead: what a person is shown, in what order, and that the product
never asks them for a number before it has given them one.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from neti.cli import app

runner = CliRunner()


def _tree(root: Path, count: int = 40) -> Path:
    work = root / "repo"
    (work / "src").mkdir(parents=True)
    for index in range(count):
        (work / "src" / f"m{index}.ts").write_text("x\n", encoding="utf-8")
    return work


def test_the_first_screen_is_not_nineteen_commands() -> None:
    """A menu nobody can read is a menu that answers nothing.

    The five evaluation commands are hidden rather than deleted — they still run, so every doc and
    golden transcript that names them still works — but they are not part of a first screen.
    """
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for internal in ("measure", "check", "score", "prove", "serve"):
        assert f" {internal} " not in result.output, (
            f"`{internal}` is a project-evaluation command and is back on the first screen"
        )
    assert "neti start" in result.output, "the first screen does not say where to start"


def test_start_is_offered_before_anything_else() -> None:
    """`Start here` has to be the first panel, or the ordering is doing nothing."""
    result = runner.invoke(app, ["--help"])
    panels = [line for line in result.output.splitlines() if line.startswith("╭─")]
    named = [p for p in panels if "Options" not in p]
    assert named, "the commands are not grouped at all"
    assert "Start here" in named[0], f"the first command panel is {named[0]!r}"


def test_start_ends_by_measuring_this_machine(tmp_path: Path) -> None:
    """The point of the first run: a fact about *your* repository, not an example about somebody
    else's. A first run that explains the product without measuring anything has not landed."""
    work = _tree(tmp_path)
    result = runner.invoke(app, ["start"], catch_exceptions=False)
    # Run from the fixture directory so the number is about it.
    import os

    cwd = os.getcwd()
    try:
        os.chdir(work)
        result = runner.invoke(app, ["start"], catch_exceptions=False)
    finally:
        os.chdir(cwd)

    assert result.exit_code == 0, result.output
    assert "objects" in result.output, "the first run never printed a magnitude"
    assert "41" in result.output or "objects" in result.output


def test_start_never_asks_for_a_ceiling(tmp_path: Path) -> None:
    """The onboarding failure underneath all the others.

    A policy says `above: 300, verdict: block`. On day one nobody knows whether 300 is generous or
    absurd for their repository, so asking for it first is asking for the one thing they cannot
    know. That still holds — nothing here prompts for a number.

    What changed is the other half. The first run used to leave a policy that could block *nothing*
    until somebody edited fourteen keys of YAML, so day one protected nothing and the median install
    never got further. It now writes a starting set and says plainly that the numbers are ours:
    sizes only ever `flag`, and the only thing that stops a call is an identity match on a file
    named in the output.
    """
    import os

    work = _tree(tmp_path)
    cwd = os.getcwd()
    try:
        os.chdir(work)
        result = runner.invoke(app, ["start"], catch_exceptions=False)
    finally:
        os.chdir(cwd)

    from neti.config.policy import load_policy

    assert "Writing a policy" in result.output
    assert "?" not in result.output.split("From here")[0].replace("Write it?", ""), (
        "the first run must never ask for a number"
    )

    policy = load_policy(work / "neti.yaml")
    gated = [(t, ptr) for t, spec in policy.tools.items() for ptr in spec.gate]
    assert gated and all(policy.gate_specs(t)[p].has_ceiling for t, p in gated), (
        "a fresh install used to protect nothing at all; that was the bug"
    )
    for tool, pointer in gated:
        verdicts = {b.verdict.name.lower() for b in policy.gate_specs(tool)[pointer].bands}
        assert verdicts == {"flag"}, (
            f"{tool}{pointer} would stop a call on a number we chose — sizes may only inform"
        )


def test_start_says_what_to_do_tomorrow(tmp_path: Path) -> None:
    """A first run that ends without a next step is a demo, not an onboarding."""
    import os

    work = _tree(tmp_path)
    cwd = os.getcwd()
    try:
        os.chdir(work)
        result = runner.invoke(app, ["start"], catch_exceptions=False)
    finally:
        os.chdir(cwd)

    for command in ("neti install", "neti report", "neti propose"):
        assert command in result.output, f"the first run never mentions `{command}`"


def test_start_is_safe_to_run_twice(tmp_path: Path) -> None:
    """Somebody will. It must not overwrite a policy they have since edited."""
    import os

    work = _tree(tmp_path)
    cwd = os.getcwd()
    try:
        os.chdir(work)
        runner.invoke(app, ["start"], catch_exceptions=False)
        (work / "neti.yaml").write_text("version: 1\nmode: observe\ntools: {}\n", encoding="utf-8")
        result = runner.invoke(app, ["start"], catch_exceptions=False)
    finally:
        os.chdir(cwd)

    assert result.exit_code == 0
    assert "already exists" in result.output
    assert (work / "neti.yaml").read_text(encoding="utf-8").strip().endswith("tools: {}")
