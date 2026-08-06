"""The walkthrough has to be true about the machine it is describing.

A first-run checklist is a promise: *these are the doors on your machine, this is the command that
works here, and this step is done.* Every one of those is checkable, and a checklist that gets any
of them wrong is worse than no checklist — somebody follows it, the thing does not work, and they
conclude the product does not.

The two properties worth pinning, in order of how much damage they do when broken:

1. **Derived, never stored.** No completion flag anywhere. Uninstall the hook and the step
   un-ticks. This is what makes it safe to show forever rather than once.
2. **Specific to this machine.** Real paths, real server names, the right scope flag. A generic
   snippet is what makes somebody close the tab and go back to the README.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neti.config.policy import Policy
from neti.core.verdict import Mode
from neti.insight.install import apply_install, plan_install
from neti.insight.onboarding import start_state

POLICY = Path("examples/coding-agent.yaml")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A project directory with nothing in it — the state of a genuine first run."""
    root = tmp_path / "repo"
    root.mkdir()
    return root


def policy_with(*, ceilings: bool = False, mode: Mode = Mode.OBSERVE) -> Policy:
    bands = [{"above": 500, "verdict": "block"}] if ceilings else []
    return Policy.model_validate(
        {
            "version": 1,
            "mode": mode,
            "tools": {"Glob": {"gate": {"/pattern": {"resolver": "fs.paths", "bands": bands}}}},
        }
    )


def state(repo: Path, **kw: object) -> object:
    return start_state(
        kw.pop("policy", policy_with()),  # type: ignore[arg-type]
        policy_path=kw.pop("policy_path", POLICY),  # type: ignore[arg-type]
        decisions=int(kw.pop("decisions", 0)),  # type: ignore[call-overload]
        root=repo,
    )


def step(st: object, name: str) -> object:
    return next(s for s in st.steps if s.id == name)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- derived, not stored


def test_installing_the_hook_ticks_the_step_and_uninstalling_un_ticks_it(repo: Path) -> None:
    """The whole reason there is no completion flag.

    A stored flag would survive somebody removing the hook, and the console would keep telling them
    they were gated while nothing was. The check is the settings file itself, every time it is
    asked — which is also what lets the console poll and complete a step under the reader's cursor.
    """
    assert not step(state(repo), "install").done  # type: ignore[attr-defined]

    apply_install(plan_install(repo, POLICY.resolve()))
    assert step(state(repo), "install").done  # type: ignore[attr-defined]

    settings = repo / ".claude" / "settings.json"
    settings.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    assert not step(state(repo), "install").done, (  # type: ignore[attr-defined]
        "the hook is gone and the step still claims it is installed"
    )


def test_the_first_recorded_call_completes_the_traffic_step(repo: Path) -> None:
    assert not step(state(repo, decisions=0), "traffic").done  # type: ignore[attr-defined]
    assert step(state(repo, decisions=1), "traffic").done  # type: ignore[attr-defined]


def test_the_last_step_needs_both_a_ceiling_and_enforcement(repo: Path) -> None:
    """A ceiling nobody enforces cannot block, and enforcement with no ceiling has nothing to
    block on. Ticking on either alone would call an install finished that cannot stop anything."""
    banded = state(repo, policy=policy_with(ceilings=True))
    enforcing = state(repo, policy=policy_with(mode=Mode.ENFORCE))
    both = state(repo, policy=policy_with(ceilings=True, mode=Mode.ENFORCE))

    assert not step(banded, "ceilings").done  # type: ignore[attr-defined]
    assert not step(enforcing, "ceilings").done  # type: ignore[attr-defined]
    assert step(both, "ceilings").done  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- about *this* machine


def test_the_command_names_the_policy_the_console_is_actually_holding(repo: Path) -> None:
    """`neti install -c <the policy in front of you>`.

    A walkthrough that prints a placeholder is a walkthrough somebody has to translate, and the
    translation is where they get it wrong — the console can be running any policy path.
    """
    st = state(repo, policy_path=Path("configs/prod.yaml"))
    assert "configs/prod.yaml" in step(st, "install").command  # type: ignore[attr-defined]


def test_the_project_hook_is_offered_before_the_user_one(repo: Path) -> None:
    """Project scope is what `neti install` does with no flags, and the scope the policy is about.

    Proposing `--user` to somebody who has not asked for it is proposing to change every session on
    their machine, which is not a checklist's call to make.
    """
    command = step(state(repo), "install").command  # type: ignore[attr-defined]

    assert command.startswith("neti install")
    assert "--user" not in command


def test_an_mcp_server_is_named_with_the_command_that_wraps_it(repo: Path) -> None:
    (repo / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"entra": {"command": "npx", "args": ["-y", "@acme/entra-mcp"]}}}
        ),
        encoding="utf-8",
    )
    st = state(repo)
    server = next(h for h in st.harnesses if h.kind == "mcp")  # type: ignore[attr-defined]

    assert server.label == "entra"
    assert server.command == "neti gate --stdio -- npx -y @acme/entra-mcp"
    assert not server.gated


def test_a_server_already_behind_the_gate_reads_as_gated_rather_than_missing(repo: Path) -> None:
    """`find_clients` skips a wrapped server, because re-wrapping would double every decision.

    Skipping it here would have told somebody who had already done the work that they had no MCP
    servers at all, which reads as discovery being broken.
    """
    (repo / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "entra": {"command": "neti", "args": ["gate", "--stdio", "--", "npx", "x"]}
                }
            }
        ),
        encoding="utf-8",
    )
    st = state(repo)
    server = next(h for h in st.harnesses if h.kind == "mcp")  # type: ignore[attr-defined]

    assert server.gated
    assert server.command == "", "there is nothing left to run for a server already gated"


def test_unparseable_settings_are_reported_rather_than_silently_dropped(repo: Path) -> None:
    """A missing row reads as "you don't run this", which is the wrong thing to tell somebody who
    does. Nothing is concluded from a file that could not be read, and it says so."""
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{not json", encoding="utf-8")

    hook = next(h for h in state(repo).harnesses if h.kind == "hook")  # type: ignore[attr-defined]

    assert not hook.gated
    assert "could not be parsed" in hook.detail


def test_a_machine_with_no_agent_at_all_still_names_the_third_door(repo: Path) -> None:
    """No Claude Code settings and no MCP servers is a real state, and the SDK seam takes any tool
    loop. An empty checklist step would leave that person with nothing to do."""
    detail = step(state(repo), "install").detail  # type: ignore[attr-defined]

    assert "Claude Code" in detail or "SDK" in detail


# --------------------------------------------------------------------------- the shape of it


def test_the_next_step_is_the_first_unfinished_one(repo: Path) -> None:
    st = state(repo)
    assert st.next_step is not None  # type: ignore[attr-defined]
    assert st.next_step.id == "install", (  # type: ignore[attr-defined]
        "a policy is loaded and its reach is readable; the gate is not wired to anything yet"
    )
    assert not st.complete  # type: ignore[attr-defined]


def test_a_finished_install_reports_complete(repo: Path) -> None:
    apply_install(plan_install(repo, POLICY.resolve()))
    st = state(repo, policy=policy_with(ceilings=True, mode=Mode.ENFORCE), decisions=12)

    assert st.complete  # type: ignore[attr-defined]
    assert st.next_step is None  # type: ignore[attr-defined]


def test_every_step_says_what_it_is_for(repo: Path) -> None:
    """A checklist with no *why* is a chore, and a chore gets skipped. Cheap to assert, and it is
    the field most likely to be left empty when a sixth step is added."""
    for s in state(repo).steps:  # type: ignore[attr-defined]
        assert s.title and s.why and s.detail, f"{s.id} is missing its copy"
