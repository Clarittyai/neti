"""What `neti init` decides about 170 real tools, pinned so a change to it has to be read.

`eval/results/mcp_coverage.json` is evidence: servers people actually install, launched and asked
`tools/list`. It is produced by `just field`, which needs Node and a network and never runs in CI.
This tier is the offline half — the same tool schemas, committed, so every change to the matcher
shows up here as a diff somebody reviews rather than as a number nobody recomputes.

The gap this closes is specific. `neti init` gated **0 of 160** discovered tools for the entire life
of the project, and no test could see it, because every fixture in the suite was a tool somebody
here wrote to be gateable. A matcher tested only against its own examples always passes.

Two files, two kinds of diff:

- `tools.json` is derived from the survey plus `builtins.py`. A refresh after `just field` moves it.
- `decisions.json` is the judgement. Only a change to `insight.discover` moves it, and every line is
  a claim about a real tool that somebody can disagree with.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from neti.insight.discover import classify
from tests.corpus import refresh

TOOLS = refresh.load_tools()
DECISIONS = json.loads(refresh.DECISIONS.read_text(encoding="utf-8"))["decisions"]


def ids(record: dict[str, Any]) -> str:
    return str(record["id"])


# ---------------------------------------------------------------------------- derived, not copied


def test_the_corpus_is_exactly_what_the_refresher_would_write() -> None:
    """It cannot be hand-edited into disagreeing with the evidence it came from.

    Both inputs are committed files, so this runs offline and needs nothing installed. A `just
    field` run somebody forgot to follow with a refresh shows up here as a diff, not as a corpus
    quietly describing last month's servers.
    """
    assert refresh.TOOLS.read_text(encoding="utf-8") == refresh.render_tools(), (
        "tools.json is stale — run `uv run python -m tests.corpus.refresh`"
    )
    assert refresh.DECISIONS.read_text(encoding="utf-8") == refresh.render_decisions(TOOLS), (
        "decisions.json no longer matches what the matcher decides — run the refresher and read "
        "the diff. Every line of it is a judgement about a real tool."
    )


def test_the_corpus_covers_every_tool_the_survey_discovered() -> None:
    survey = json.loads(refresh.SURVEY.read_text(encoding="utf-8"))
    discovered = {
        f"mcp:{server['name']}:{tool['name']}"
        for server in survey["servers"]
        if server.get("launched")
        for tool in server["tools"]
    }
    assert discovered <= {r["id"] for r in TOOLS}


def test_the_corpus_carries_claude_codes_own_built_ins() -> None:
    """The tools most agent calls in the world actually are, and the ones no `tools/list` reports.

    They are the harness's built-ins, which is exactly why `PreToolUse` exists — so the survey in
    `eval/` structurally cannot reach them and they are authored. Asserted by name rather than
    inferred, because a corpus that silently lost `Glob` would still look full.
    """
    present = {r["name"] for r in TOOLS if r["source"] == "claude-code"}
    assert {"Read", "Write", "Edit", "Glob", "Grep", "Bash", "NotebookEdit"} <= present


# ---------------------------------------------------------------------------- the judgements


@pytest.mark.parametrize("record", TOOLS, ids=ids)
def test_the_matcher_still_decides_what_the_corpus_says(record: dict[str, Any]) -> None:
    """One test per tool, so a failure names the tool rather than a count."""
    spec = classify({"name": record["name"], "inputSchema": record["schema"]})
    expected = DECISIONS[record["id"]]
    assert [
        {"pointer": g.pointer, "resolver": g.resolver, "unit": g.unit} for g in spec.gated
    ] == expected["gated"]


@pytest.mark.parametrize("record", TOOLS, ids=ids)
def test_no_parameter_falls_through_unremarked(record: dict[str, Any]) -> None:
    """Every parameter is either gated or declined *with a reason*.

    This is what makes "sizeable but ungated" impossible to accumulate silently. A parameter some
    resolver could genuinely size cannot be given an honest one-line reason it was skipped, so the
    only way to satisfy this is to gate it or to write down why not — and the reason lands in the
    generated policy, where an operator can overrule it.
    """
    spec = classify({"name": record["name"], "inputSchema": record["schema"]})
    accounted = {g.pointer.lstrip("/").split("#")[0] for g in spec.gated} | {
        d.param for d in spec.declined
    }
    assert accounted == set(spec.params)
    assert all(d.why for d in spec.declined), "a decline without a reason is a silent gap"


def test_the_filesystem_tools_a_coding_agent_uses_all_day_are_gated() -> None:
    """The claim `examples/coding-agent.yaml` makes by hand, made by the matcher on its own.

    Until now nothing checked that `neti init` would arrive at that file's gates if it met the tools
    cold — the example was written by somebody who already knew the answer.
    """
    for name in ("Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit"):
        gated = DECISIONS[f"claude-code:builtin:{name}"]["gated"]
        assert any(g["resolver"] == "fs.paths" for g in gated), f"{name} is not sized at all"


def test_bash_is_gated_by_nothing_and_that_is_the_decision() -> None:
    """SCOPE.md NC-09 and NC-10, as a test rather than a paragraph.

    Sizing a shell command means parsing a grammar instead of reading a value, and a gate that
    guesses makes a weaker claim than one that measures. `Bash` is in the corpus precisely so that
    this is visible: leaving the most-used tool there is out would hide the same statement.
    """
    assert DECISIONS["claude-code:builtin:Bash"]["gated"] == []


def test_a_search_query_is_never_read_as_sql() -> None:
    """The single worst over-claim available, and the one the old hand-written map made seven times.

    `db.rows` would decline a web search anyway — but only after an operator had committed a gate to
    it and started reading UNRESOLVED on every call, which is the wrong place to find out.
    """
    searches = [
        r["id"]
        for r in TOOLS
        if "search" in r["name"].lower() and "query" in (r["schema"].get("properties") or {})
    ]
    assert searches, "the corpus should contain search tools taking a `query`"
    for tool_id in searches:
        assert not any(g["resolver"] == "db.rows" for g in DECISIONS[tool_id]["gated"]), tool_id


def test_a_repository_path_is_never_counted_as_a_local_one() -> None:
    """`get_file_contents(owner, repo, path)` — `path` there is inside a repository.

    A local walk would count a different tree and report the number without hesitating, which is
    worse than not answering. 21 of the old map's 43 claims were this shape.
    """
    for record in TOOLS:
        props = record["schema"].get("properties") or {}
        if {"owner", "repo"} <= set(props):
            gated = DECISIONS[record["id"]]["gated"]
            assert not any(g["resolver"] == "fs.paths" for g in gated), record["id"]


def test_the_corpus_decides_the_same_way_against_the_raw_evidence() -> None:
    """M10 recomputed from the survey's *schemas*, not from the verdicts it cached alongside them.

    The distinction matters and cost a confusing failure to find. `eval/results/mcp_coverage.json`
    stores both — the tool schemas, which are evidence and only change when a server does, and the
    gated/ungated verdicts, which are judgement and change whenever the matcher does. Comparing
    against the cached verdicts makes this test fail every time the matcher improves, until somebody
    re-runs a survey that needs Node and a network. CI cannot depend on that.

    So this recomputes. What it pins is that the corpus is a faithful projection of the evidence —
    which is the claim worth having offline. Whether the committed survey's own cached numbers are
    stale is a separate question, and `python -m tests.corpus.refresh --check` is where it is asked.
    """
    survey = json.loads(refresh.SURVEY.read_text(encoding="utf-8"))
    recomputed = sum(
        1
        for server in survey["servers"]
        if server.get("launched")
        for tool in server["tools"]
        if classify({"name": tool["name"], "inputSchema": tool.get("schema") or {}}).gated
    )
    from_corpus = sum(
        1 for record in TOOLS if record["source"] == "mcp" and DECISIONS[record["id"]]["gated"]
    )
    assert from_corpus == recomputed
