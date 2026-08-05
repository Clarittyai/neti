"""One oversized call, every popular runtime's own loop, no model anywhere.

See `runtimes.py` for why this exists separately from the seam table. The assertions here are
deliberately few and hard to satisfy by accident:

1. **The tool function never executed.** Not "an error came back" — the body did not run. A
   framework that reported a refusal and ran the call anyway would pass a weaker check.
2. **The sentence is byte-for-byte what `Preflight` produced.** Computed here rather than pinned as
   a literal, so it tracks the product; what is being asserted is that eleven code paths did not
   reword the one thing the model reads.
3. **The decision was sealed.** A gate that stops a call and records nothing is not auditable, and
   every runtime writes into the same chain.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from neti.preflight import Preflight
from neti.store.jsonl import JsonlSink, read_records
from tests.conformance.runtimes import RUNTIMES, TOOL, Runtime
from tests.e2e import worlds

ARGS = {"pattern": "{tree}/**/*.txt"}
MAGNITUDE = 30


def _missing(runtime: Runtime) -> str | None:
    for module in runtime.needs:
        try:
            if importlib.util.find_spec(module) is None:
                return module
        except (ImportError, ValueError):
            return module
    return None


@pytest.fixture
def world(tmp_path: Path) -> Any:
    return worlds.build_world("fs", worlds.build_fixtures(tmp_path))


def _expected(world: Any, tmp_path: Path, args: dict[str, Any]) -> str:
    """What the gate says with no framework involved. The baseline every row must match."""
    preflight = Preflight(engine=world.engine(), sink=JsonlSink(tmp_path / "baseline.ndjson"))
    verdict = preflight.check(TOOL, args)
    assert verdict.verdict == "block", "the fixture stopped being a blocked call"
    assert verdict.payload.get("resolved") == MAGNITUDE
    return str(verdict.message)


@pytest.mark.parametrize("runtime", RUNTIMES, ids=lambda r: r.name)
def test_the_framework_runs_its_own_loop_and_the_call_is_stopped(
    runtime: Runtime, world: Any, tmp_path: Path
) -> None:
    missing = _missing(runtime)
    if missing:
        pytest.skip(f"{missing} is not installed")

    fixtures = worlds.build_fixtures(tmp_path / "tree")
    inner = worlds.build_world("fs", fixtures)
    args = worlds.render(ARGS, fixtures)
    expected = _expected(inner, tmp_path, args)

    records = tmp_path / f"{runtime.name}.ndjson"
    preflight = Preflight(engine=inner.engine(), sink=JsonlSink(records))
    driven = runtime.drive(preflight, args)

    assert not driven.ran, (
        f"{runtime.name} executed the tool body despite a BLOCK verdict — the gate is not in this "
        "framework's execution path, whatever the adapter's own tests say"
    )
    assert driven.sentence == expected, (
        f"{runtime.name} handed the model a different sentence to every other runtime.\n"
        f"  expected: {expected!r}\n"
        f"       got: {driven.sentence!r}\n"
        "The sentence is what makes an agent narrow its target, so a reworded one is a different "
        "product on that runtime."
    )

    sealed = list(read_records(records))
    assert sealed, f"{runtime.name} stopped the call without recording it"
    assert sealed[-1].verdict == "block"
    assert sealed[-1].causes[0]["magnitude"] == MAGNITUDE


def test_every_runtime_agrees_with_every_other(world: Any, tmp_path: Path) -> None:
    """The cross-runtime claim, asserted once rather than inferred from N passing rows.

    Each row above compares itself to the baseline, which is *nearly* the same thing — but "they
    all match a constant" is the claim somebody actually wants, and reading it out of four separate
    parametrised results is exactly the kind of inference that quietly stops being checked.
    """
    fixtures = worlds.build_fixtures(tmp_path / "tree")
    inner = worlds.build_world("fs", fixtures)
    args = worlds.render(ARGS, fixtures)

    sentences: dict[str, str] = {}
    for runtime in RUNTIMES:
        if _missing(runtime):
            continue
        preflight = Preflight(
            engine=inner.engine(), sink=JsonlSink(tmp_path / f"agree-{runtime.name}.ndjson")
        )
        sentences[runtime.name] = runtime.drive(preflight, args).sentence

    if len(sentences) < 2:
        pytest.skip("fewer than two runtimes installed, so there is nothing to compare")
    assert len(set(sentences.values())) == 1, f"runtimes disagree: {sentences}"


def test_crewai_hooks_alone_cannot_tell_the_model_the_number(tmp_path: Path) -> None:
    """The defect this directory was written to find, kept where it cannot come back.

    `crewai_hooks.install` blocks the call and records it correctly, and the model is told only
    `Tool execution blocked by hook. Tool: Glob`. CrewAI returns that fixed string the instant a
    before-hook answers `False`, and the `after_tool_call` hook that was supposed to substitute the
    real sentence is never reached — there is one call site for the pair and it returns early.

    The adapter previously documented the pairing as working, and the seam test agreed with it,
    because that test *imitated* CrewAI's control flow and imitated it wrongly. So this asserts the
    framework's real behaviour rather than the adapter's intent: if a future CrewAI runs after-hooks
    on a blocked call, this fails, and that is the correct moment to reconsider the design.
    """
    pytest.importorskip("crewai")
    from crewai import Agent, Crew, Task
    from crewai.llms.base_llm import BaseLLM
    from crewai.tools import BaseTool

    from neti.adapters import crewai_hooks

    fixtures = worlds.build_fixtures(tmp_path / "tree")
    inner = worlds.build_world("fs", fixtures)
    args = worlds.render(ARGS, fixtures)
    preflight = Preflight(engine=inner.engine(), sink=JsonlSink(tmp_path / "hooks.ndjson"))

    ran: list[str] = []
    seen: list[str] = []

    class GlobTool(BaseTool):
        name: str = TOOL
        description: str = "Match files."

        def _run(self, pattern: str) -> str:
            ran.append(pattern)
            return "ran"

    class Scripted(BaseLLM):
        turns: int = 0

        def call(self, messages: Any, **kw: Any) -> str:
            self.turns += 1
            seen.append(str(messages))
            if self.turns == 1:
                import json

                return f"Thought: look\nAction: {TOOL}\nAction Input: " + json.dumps(args)
            return "Thought: done\nFinal Answer: stopped"

        def supports_function_calling(self) -> bool:
            return False

    crewai_hooks.clear()
    crewai_hooks.install(preflight)
    try:
        agent = Agent(
            role="ops",
            goal="measure",
            backstory="tests",
            llm=Scripted(model="scripted"),
            tools=[GlobTool()],
            verbose=False,
        )
        Crew(
            agents=[agent],
            tasks=[Task(description="go", expected_output="text", agent=agent)],
            verbose=False,
        ).kickoff()
    finally:
        crewai_hooks.clear()

    shown = "\n".join(seen)
    assert not ran, "the hook did not stop the call at all"
    assert "Tool execution blocked by hook" in shown, (
        "CrewAI no longer substitutes its fixed string — re-read the adapter, the pairing may work "
        "now"
    )
    assert str(MAGNITUDE) not in shown, (
        "the agent was given the magnitude through the hook path, which would mean after-hooks now "
        "run on a blocked call. If so, `install` can carry the sentence and this test should go."
    )

    sealed = list(read_records(tmp_path / "hooks.ndjson"))
    assert sealed and sealed[-1].verdict == "block", (
        "the hook path must still stop and record the call — what it cannot do is explain it"
    )


def test_no_runtime_claims_a_depth_it_did_not_reach() -> None:
    """`agent_loop` and `executor` are different claims and the table must not blur them."""
    from tests.conformance.runtimes import AGENT_LOOP, EXECUTOR

    for runtime in RUNTIMES:
        assert runtime.depth in {AGENT_LOOP, EXECUTOR}, runtime.name
        assert runtime.what.strip(), f"{runtime.name} does not say what it drove"
