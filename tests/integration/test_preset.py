"""Day zero, and the line that keeps it honest.

A fresh install used to protect nothing and could not — nine gated parameters, zero ceilings,
observe mode — until somebody edited a 210-line YAML file. Eight steps and a week stood between
`pip install` and any protection, and the median install never finished them.

The founding principle is right about a *tuned* ceiling and wrong about a catastrophic one. So day
zero now protects, and the property that makes that defensible is asserted first below:

> **Day zero never blocks on a number we chose.**

Everything derived from a size is `flag` — recorded, notified, and the call proceeds. The only
day-zero verdict that stops a call is an identity match on a file the operator was shown by name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neti.config.policy import Policy, load_policy
from neti.core.types import ProposedCall
from neti.engine import Engine
from neti.insight.edit_policy import apply_preset, plan_preset
from neti.insight.preset import FLOOR, build, threshold
from neti.resolvers.filesystem import FilesystemResolver

SHIPPED = Path("examples/coding-agent.yaml")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    for i in range(30):
        (tmp_path / "src" / f"m{i}.ts").write_text("x", encoding="utf-8")
    (tmp_path / ".env").write_text("STRIPE=sk_live", encoding="utf-8")
    return tmp_path


def policy_at(repo: Path) -> Path:
    target = repo / "neti.yaml"
    target.write_text(SHIPPED.read_text(encoding="utf-8"), encoding="utf-8")
    return target


# --------------------------------------------------------------------------- the honesty property


def test_nothing_in_the_preset_blocks_on_a_size(repo: Path) -> None:
    """The claim the whole feature rests on, asserted directly.

    We are allowed to choose a threshold and tell somebody about it. We are not allowed to stop
    their work on a number they never picked — that is what `neti propose` is for, and it reads
    their own traffic.
    """
    preset = build(repo, reach=60_000)

    for band in preset.as_policy()["bands"]:
        assert band["verdict"] == "flag", "a size we chose may inform, never stop"

    for rule in preset.as_policy()["sensitive"]:
        assert rule["verdict"] in {"confirm", "block"}, "identity may stop; it is not a guess"


def test_a_small_repository_gets_no_threshold_worth_tripping(repo: Path) -> None:
    """The floor. A twenty-four-file repository flagging its own three-file glob is the noise that
    gets this uninstalled in an afternoon, and there is nothing catastrophic to do there anyway."""
    assert threshold(24) == FLOOR
    assert threshold(0) == FLOOR


def test_the_threshold_scales_with_the_tree(repo: Path) -> None:
    assert threshold(5_011) == 501
    assert threshold(59_330) == 5_933
    assert threshold(1_200_000) == 120_000


def test_off_limits_rules_are_only_for_what_is_really_there(repo: Path) -> None:
    assert [c.match for c in build(repo, 1_000).off_limits] == ["**/.env*"]

    bare = repo / "empty"
    bare.mkdir()
    assert build(bare, 1_000).off_limits == []


# --------------------------------------------------------------------------- what start leaves


def test_a_fresh_install_protects_something(repo: Path) -> None:
    """The whole point, stated as the inverse of what shipped before.

    Before: `mode: observe`, 9 gated parameters, **0** ceilings, 0 off-limits rules — a gate that
    could block nothing at all until a human learned fourteen policy keys.
    """
    target = policy_at(repo)
    preset = build(repo, 60_000)
    apply_preset(
        plan_preset(
            target,
            bands=[{"above": preset.flag_above, "verdict": "flag"}],
            rules=[
                {"match": c.match, "verdict": c.verdict, "why": c.why} for c in preset.off_limits
            ],
        )
    )

    policy = load_policy(target)
    gates = [(t, p) for t, s in policy.tools.items() for p in s.gate]

    assert policy.mode.name.lower() == "enforce"
    assert gates, "the shipped example still gates something"
    assert all(policy.gate_specs(t)[p].has_ceiling for t, p in gates)
    assert [r.match for r in policy.sensitive] == ["**/.env*"]


def test_it_keeps_every_comment_and_backs_up_the_original(repo: Path) -> None:
    """`examples/coding-agent.yaml` is more comment than configuration, and those comments are most
    of its value. And the backup has to be what the operator started with — see `PresetEdit`."""
    target = policy_at(repo)
    original = target.read_text(encoding="utf-8")
    comments = sum(1 for line in original.splitlines() if line.strip().startswith("#"))

    apply_preset(
        plan_preset(
            target,
            bands=[{"above": 500, "verdict": "flag"}],
            rules=[{"match": "**/.env*", "verdict": "confirm", "why": "credentials"}],
        )
    )

    after = target.read_text(encoding="utf-8")
    assert sum(1 for line in after.splitlines() if line.strip().startswith("#")) == comments
    assert target.with_suffix(".yaml.bak").read_text(encoding="utf-8") == original


def test_a_ceiling_somebody_committed_is_never_overwritten(repo: Path) -> None:
    """The preset is what to do when nobody has decided yet. Replacing a number an operator
    committed with one we chose would be the opposite of the argument for having it."""
    target = policy_at(repo)
    text = target.read_text(encoding="utf-8").replace(
        "        resolver: shell.paths\n",
        "        resolver: shell.paths\n        bands:\n          - { above: 7, verdict: block }\n",
        1,
    )
    target.write_text(text, encoding="utf-8")

    apply_preset(plan_preset(target, bands=[{"above": 9_999, "verdict": "flag"}], rules=[]))

    bands = load_policy(target).gate_specs("Bash")["/command"].bands
    assert [(b.above, b.verdict.name.lower()) for b in bands] == [(7, "block")]


# --------------------------------------------------------------------------- how it behaves


def engine_for(target: Path, root: Path) -> Engine:
    """The shipped example gates `Bash` on `shell.paths` too, and the engine refuses a policy
    naming a resolver it does not have — correctly, since a misspelled resolver is silent dead
    config. Both are bound here rather than narrowing the fixture, so these run against the real
    policy a user is actually handed."""
    from neti.resolvers.shell import ShellPathsResolver

    return Engine(
        policy=load_policy(target),
        resolvers={
            "fs.paths": FilesystemResolver(root=root),
            "shell.paths": ShellPathsResolver(root=str(root)),
        },
    )


def verdict_of(engine: Engine, tool: str, arg: str, value: str) -> str:
    return engine.gate(ProposedCall(tool=tool, args={arg: value})).decision.verdict.name


def test_ordinary_work_is_silent_and_secrets_are_not(repo: Path) -> None:
    """The two halves of whether anybody keeps this installed."""
    target = policy_at(repo)
    preset = build(repo, 60_000)
    apply_preset(
        plan_preset(
            target,
            bands=[{"above": preset.flag_above, "verdict": "flag"}],
            rules=[
                {"match": c.match, "verdict": c.verdict, "why": c.why} for c in preset.off_limits
            ],
        )
    )
    engine = engine_for(target, repo)

    assert verdict_of(engine, "Read", "file_path", str(repo / "src" / "m1.ts")) == "ALLOW"
    assert verdict_of(engine, "Glob", "pattern", str(repo / "src" / "*.ts")) == "ALLOW"
    assert verdict_of(engine, "Read", "file_path", str(repo / ".env")) == "CONFIRM"


def test_a_large_call_flags_and_still_proceeds(repo: Path) -> None:
    """Recorded, surfaced, notified — and never stopped, because the number was ours."""
    for i in range(700):
        (repo / "src" / f"extra{i}.ts").write_text("x", encoding="utf-8")

    target = policy_at(repo)
    apply_preset(plan_preset(target, bands=[{"above": FLOOR, "verdict": "flag"}], rules=[]))
    engine = engine_for(target, repo)

    result = engine.gate(ProposedCall(tool="Glob", args={"pattern": str(repo / "src" / "*.ts")}))
    assert result.decision.verdict.name == "FLAG"
    assert result.proceeds, "a size we chose must never stop the work"


def test_the_written_policy_is_the_one_that_decides(repo: Path) -> None:
    """It has to load through the ordinary path, not just through the editor that wrote it."""
    target = policy_at(repo)
    apply_preset(
        plan_preset(
            target,
            bands=[{"above": 500, "verdict": "flag"}],
            rules=[{"match": "**/.env*", "verdict": "confirm", "why": "credentials"}],
        )
    )
    assert isinstance(load_policy(target), Policy)


def test_running_it_twice_changes_nothing(repo: Path) -> None:
    """Somebody will run `neti start` again, and it must be a no-op rather than a slow corruption.

    It was not. `_enforcing` replaced the first `observe` anywhere on the line, which works exactly
    once — the shipped policy reads `mode: observe # observe | enforce. …`, so after one pass the
    value is right and the first remaining `observe` is *inside the comment*. A second run rewrote
    the documentation to `# enforce | enforce`.

    Found by reading the diff of the commit that introduced it, which had done precisely that to
    this repository's own policy.
    """
    target = policy_at(repo)
    preset = build(repo, 60_000)

    def apply_once() -> str:
        apply_preset(
            plan_preset(
                target,
                bands=[{"above": preset.flag_above, "verdict": "flag"}],
                rules=[
                    {"match": c.match, "verdict": c.verdict, "why": c.why}
                    for c in preset.off_limits
                ],
            )
        )
        return target.read_text(encoding="utf-8")

    once = apply_once()
    assert once == apply_once(), "a second run must be a no-op, not a second edit"
    assert "# observe | enforce" in once, "the comment explaining the modes was rewritten"
