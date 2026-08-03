"""`neti install` — writing into a config file somebody else owns.

Installing used to be four steps, the last of which was hand-editing `.claude/settings.json` to a
shape you had to copy correctly, in a file an agent depends on, with no feedback until the next
session behaved strangely. That is where evaluators stop.

Writing to a user's config is also the most destructive thing this product does, so the tests here
are mostly about restraint:

- an existing hook belonging to somebody else survives
- everything outside `hooks` survives
- running twice does not install twice — two hooks would both fire, doubling every decision and
  writing each one to the chain twice
- settings that cannot be parsed are refused rather than overwritten
- a policy that does not load is refused, because a hook pointing at one runs on *every* tool call
  and fails on every one
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from neti.insight.install import apply_install, plan_install, settings_path

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "coding-agent.yaml"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "neti.yaml").write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def settings_of(project: Path) -> dict:
    return json.loads(settings_path(project).read_text(encoding="utf-8"))


def neti(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "neti.cli", *args], capture_output=True, text=True, cwd=cwd
    )


# ---------------------------------------------------------------------------- the plan


def test_it_adds_the_hook_to_an_empty_project(project: Path) -> None:
    plan = plan_install(project, project / "neti.yaml")

    entries = plan.after["hooks"]["PreToolUse"]
    assert len(entries) == 1
    assert entries[0]["matcher"] == "*"
    assert "neti hook" in entries[0]["hooks"][0]["command"]


def test_someone_elses_hook_survives(project: Path) -> None:
    """The case that matters most. A `hooks` block usually has entries in it already, and clobbering
    them breaks tooling the user set up before they heard of this."""
    path = settings_path(project)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(git status)"]},
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    plan = plan_install(project, project / "neti.yaml")

    commands = [h["command"] for e in plan.after["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert "my-linter" in commands
    assert plan.other_hooks == 1
    assert plan.after["permissions"] == {"allow": ["Bash(git status)"]}


def test_installing_twice_installs_once(project: Path) -> None:
    """Two hooks would both fire: every decision doubled, and written to the chain twice."""
    apply_install(plan_install(project, project / "neti.yaml"))
    second = plan_install(project, project / "neti.yaml")

    assert second.already_installed
    assert len(second.after["hooks"]["PreToolUse"]) == 1


def test_pointing_at_a_new_policy_updates_rather_than_appends(project: Path) -> None:
    """Re-running with a different `-c` should move the hook, not add a competing one."""
    apply_install(plan_install(project, project / "neti.yaml"))
    (project / "other.yaml").write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")

    plan = plan_install(project, project / "other.yaml")
    entries = plan.after["hooks"]["PreToolUse"]

    assert len(entries) == 1
    assert not plan.already_installed
    assert entries[0]["hooks"][0]["command"].endswith("other.yaml")


def test_settings_that_cannot_be_parsed_are_refused(project: Path) -> None:
    """Never overwrite what we could not read. Guessing at the user's intent here is how a config
    gets destroyed."""
    path = settings_path(project)
    path.parent.mkdir(parents=True)
    path.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        plan_install(project, project / "neti.yaml")


def test_a_hooks_block_of_the_wrong_shape_is_refused(project: Path) -> None:
    path = settings_path(project)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"hooks": {"PreToolUse": "not-a-list"}}), encoding="utf-8")

    with pytest.raises(ValueError, match="not a list"):
        plan_install(project, project / "neti.yaml")


def test_the_original_is_kept(project: Path) -> None:
    path = settings_path(project)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"permissions": {"allow": []}}), encoding="utf-8")

    backup = apply_install(plan_install(project, project / "neti.yaml"))

    assert backup is not None and backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8")) == {"permissions": {"allow": []}}


def test_user_scope_writes_to_the_home_settings(
    project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Project scope is the default because a policy is about one repository — its ceilings came
    from that repository's traffic and its `providers.fs.root` names that tree."""
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    assert settings_path(project, user=True) == home / ".claude" / "settings.json"
    assert settings_path(project) == project / ".claude" / "settings.json"


# ---------------------------------------------------------------------------- through the CLI


def test_the_command_writes_a_working_hook(project: Path) -> None:
    out = neti("install", "--yes", cwd=project)
    assert out.returncode == 0, out.stderr

    command = settings_of(project)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command.startswith("neti hook -c ")

    # The whole point is that the command it wrote actually gates. Run the same one.
    event = json.dumps({"tool_name": "Glob", "tool_input": {"pattern": str(project)}})
    hook = subprocess.run(
        [sys.executable, "-m", "neti.cli", *command.split()[1:]],
        input=event,
        capture_output=True,
        text=True,
        cwd=project,
    )
    assert hook.returncode == 0, hook.stderr


def test_it_refuses_a_policy_that_does_not_load(project: Path) -> None:
    """A hook pointing at a broken policy runs on every tool call and fails on every one of them.
    Better to refuse at install than to discover it mid-session."""
    (project / "broken.yaml").write_text(
        "version: 1\ntools:\n  x:\n    gate:\n      /y: {resolver: nope}\n", encoding="utf-8"
    )

    out = neti("install", "-c", "broken.yaml", "--yes", cwd=project)

    assert out.returncode == 2
    assert "does not load" in out.stderr
    assert not settings_path(project).exists(), "nothing may be written when the policy is bad"


def test_it_refuses_when_there_is_no_policy_and_says_how_to_get_one(tmp_path: Path) -> None:
    out = neti("install", "--yes", cwd=tmp_path)

    assert out.returncode == 2
    assert "no policy" in out.stderr
    assert "neti init" in out.stderr, "a refusal has to say what to do instead"


def test_it_shows_the_change_before_making_it(project: Path) -> None:
    """This writes to a file the user owns and an agent depends on. Printing the result is the
    difference between a tool somebody trusts with their config and one they run in a VM."""
    out = neti("install", "--yes", cwd=project)

    assert "Will write" in out.stdout
    assert "PreToolUse" in out.stdout


def test_it_says_when_the_policy_will_not_block_anything(project: Path) -> None:
    """The shipped examples are observe-mode. Somebody installing one should know that nothing will
    be stopped yet, or they will conclude the gate does not work."""
    out = neti("install", "--yes", cwd=project)

    assert "observe mode" in out.stdout
