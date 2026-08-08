"""Three states that look identical from a terminal, and two of them are false safety.

A gate working correctly on an ordinary week does nothing visible. So does one wired to a policy
that moved, and so does one that was never wired at all:

    working, and nothing happened          the good case
    wired to a policy that moved           silent, and protecting nothing
    never wired at all                     silent, and protecting nothing

`neti report` answers *what happened*, which cannot separate them when the answer is "nothing".
Every test here is one of those states, asserted on the operator-facing answer rather than on the
internals — because the failure mode is a person reading a screen and believing the wrong thing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neti.insight.status import ago, build_status, observed, render

SHIPPED = Path("examples/coding-agent.yaml")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A started project, with the user-level settings pointed somewhere empty.

    `~/.claude/settings.json` is read as a fallback, correctly — a gate wired globally protects this
    project too. In a test that means the developer's own machine decides the answer, so `HOME` is
    moved somewhere empty and the fallback has nothing to find.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("x", encoding="utf-8")
    (tmp_path / ".env").write_text("K=1", encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    from neti.insight.edit_policy import apply_preset, plan_preset

    config = tmp_path / "neti.yaml"
    config.write_text(SHIPPED.read_text(encoding="utf-8"), encoding="utf-8")
    apply_preset(
        plan_preset(
            config,
            bands=[{"above": 500, "verdict": "flag"}],
            rules=[{"match": "**/.env*", "verdict": "confirm", "why": "credentials live here"}],
            outside_root="confirm",
        )
    )
    return tmp_path


def wire(project: Path, policy: Path) -> None:
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": f"neti hook -c {policy}"}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def test_the_good_case_says_so_in_the_first_line(project: Path) -> None:
    wire(project, project / "neti.yaml")
    status = build_status(project, "neti.yaml")

    assert status.live
    assert status.fix == "", "nothing is wrong, so nothing should be suggested"
    assert render(status, 12, None, 0).startswith("neti is on and enforcing.")


def test_never_wired_reads_as_not_protecting(project: Path) -> None:
    """The state a fresh `neti start` leaves when the operator skips the install prompt."""
    status = build_status(project, "neti.yaml")

    assert not status.live
    assert status.fix == "neti install"
    assert "NOT protecting" in render(status, 0, None, 0)


def test_wired_to_a_policy_that_moved_is_its_own_finding(project: Path) -> None:
    """The failure a rename creates, and the one nothing else would ever surface.

    The hook is present, the command runs, every call really is gated — against a policy that is
    not this one. Every other signal in the product looks healthy: `neti report` shows traffic,
    the chain verifies, the console loads. The operator is protected by rules they are not reading.
    """
    wire(project, project / "somewhere-else" / "neti.yaml")
    status = build_status(project, "neti.yaml")

    assert not status.live
    wired = next(c for c in status.checks if c.label == "wired into Claude Code")
    same = next(c for c in status.checks if c.label == "wired to THIS policy")
    assert wired.ok is True, "the hook really is installed, and saying otherwise sends them wrong"
    assert same.ok is False
    assert "somewhere-else" in same.detail, "it has to name the file it is actually using"


def test_a_global_hook_counts(project: Path) -> None:
    """Wired at `~/.claude` protects this project too.

    Reporting "not wired" to somebody who wired it globally sends them to install it a second time,
    and two hooks both fire — doubling every decision and every entry in the chain.
    """
    wire(project / "home", project / "neti.yaml")

    status = build_status(project, "neti.yaml")
    wired = next(c for c in status.checks if c.label == "wired into Claude Code")
    assert wired.ok is True
    assert wired.detail == "user settings"


def test_observe_mode_is_not_protection(project: Path) -> None:
    """Recorded and forwarded is a different claim from stopped, and the word for it is not `on`."""
    config = project / "neti.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("mode: enforce", "mode: observe", 1),
        encoding="utf-8",
    )
    wire(project, config)

    status = build_status(project, "neti.yaml")
    assert not status.live
    assert "nothing is stopped" in next(c for c in status.checks if c.label == "enforcing").detail


def test_a_policy_that_will_not_load_is_the_loudest_case(project: Path) -> None:
    """`neti hook` exits 0 on a policy error, so this is a session running entirely ungated.

    Nothing else says so: the hook is wired, the agent runs, every call passes, and the reason is on
    stderr where no one reads it.
    """
    (project / "neti.yaml").write_text("tools: [this is not\n  valid yaml", encoding="utf-8")
    wire(project, project / "neti.yaml")

    status = build_status(project, "neti.yaml")
    assert not status.live
    assert any(c.label == "the policy loads" and c.ok is False for c in status.checks)


def test_no_policy_at_all_sends_you_to_start(project: Path) -> None:
    (project / "neti.yaml").unlink()
    status = build_status(project, "neti.yaml")

    assert not status.live
    assert status.fix == "neti start"


# --------------------------------------------------------------------------- what it has seen


def test_an_empty_chain_is_never_rendered_as_safe(project: Path) -> None:
    """The whole point. "Nothing happened" and "nothing is reaching the gate" are different facts,
    and the second one is only visible if the screen says it out loud."""
    wire(project, project / "neti.yaml")
    text = render(build_status(project, "neti.yaml"), 0, None, 0)

    assert "nothing yet" in text
    assert "not going through the hook" in text


def test_it_counts_what_the_chain_holds(tmp_path: Path) -> None:
    records = tmp_path / "d.ndjson"
    records.write_text(
        "\n".join(
            json.dumps({"verdict": v, "decided_at": f"2026-08-0{i + 1}T00:00:00+00:00"})
            for i, v in enumerate(["allow", "confirm", "allow", "block"])
        ),
        encoding="utf-8",
    )
    seen, last, stopped = observed(records)

    assert (seen, stopped) == (4, 2), "confirm and block are what stopped a call; flag proceeds"
    assert last == "2026-08-04T00:00:00+00:00"


def test_a_missing_records_file_is_zero_and_not_a_crash(tmp_path: Path) -> None:
    assert observed(tmp_path / "nothing.ndjson") == (0, None, 0)


def test_a_half_written_line_does_not_lose_the_rest(tmp_path: Path) -> None:
    """The sink appends, so the last line can be torn. Counting is not worth failing over."""
    records = tmp_path / "d.ndjson"
    records.write_text(
        json.dumps({"verdict": "allow", "decided_at": "2026-08-01T00:00:00+00:00"})
        + "\n{\"verdict\": \"conf",
        encoding="utf-8",
    )
    seen, _last, _stopped = observed(records)
    assert seen == 1


def test_never_reads_as_never(tmp_path: Path) -> None:
    assert ago(None) == "never"
    assert ago("not a timestamp") == "not a timestamp"


def test_settings_we_cannot_parse_are_reported_as_unknown(project: Path) -> None:
    """Not "wired", not "not wired". `??`.

    Reporting a confident no here sends somebody to `neti install`, which refuses to overwrite an
    unreadable settings file and would fail in front of them — a fix that cannot work is worse than
    no fix. Reporting yes would be worse still. This is the one place the three-valued check earns
    its existence, so it is asserted rather than assumed.
    """
    settings = project / ".claude"
    settings.mkdir(exist_ok=True)
    (settings / "settings.json").write_text("{ not json", encoding="utf-8")

    status = build_status(project, "neti.yaml")
    wired = next(c for c in status.checks if c.label == "wired into Claude Code")

    assert wired.ok is None
    assert wired.mark == "??"
    assert not status.live, "unknown is not live"
    assert "neti install" not in status.fix, "a fix that would itself fail is not a fix"
    assert "settings.json" in status.fix


def test_the_wiring_verdict_comes_from_the_installer(project: Path) -> None:
    """One source of truth for one fact.

    Whether the hook is wired correctly is `plan_install(...).already_installed` — the same
    comparison `neti install` makes. A second parse deciding it here would drift the day the command
    format changes, and this status screen would then confidently report the wrong thing about the
    one question it exists to answer.

    The parse that remains does only what `already_installed` cannot: name the other policy.
    """
    import neti.insight.install as install

    wire(project, project / "neti.yaml")
    calls: list[bool] = []
    original = install.plan_install

    def counted(*args: object, **kwargs: object) -> object:
        calls.append(True)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    install.plan_install = counted  # type: ignore[assignment]
    try:
        status = build_status(project, "neti.yaml")
    finally:
        install.plan_install = original  # type: ignore[assignment]

    assert calls, "the wiring check did not ask the installer"
    assert status.live
