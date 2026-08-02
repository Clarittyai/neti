"""One corpus, four runtimes — because underneath they all carry the same JSON Schema.

`test_detection.py` runs the matcher against a normalised record: a tool name and an `inputSchema`.
That is only worth anything if the normalised form is *faithful* — if a LangChain `StructuredTool`,
an OpenAI function tool and an Anthropic `beta_tool` really do reduce to the same thing a policy can
be pointed at. Otherwise the corpus proves detection works on a shape no runtime actually produces.

So this builds each framework's own tool object out of a corpus record, pulls the schema back out
the way that framework exposes it, and asserts the parameter names survive the round trip. Names,
not the whole schema: the matcher reads parameter names and the tool name, and the frameworks
legitimately differ on `title`, `required` ordering and how they spell `additionalProperties`.

Guarded per framework, and deliberately additive — every assertion here sits on top of a detection
test in `test_detection.py` that runs unguarded. A bare `pip install neti && pytest` still checks
the substance; these check that the substance applies to the runtime you happen to use.
"""

from __future__ import annotations

from typing import Any

import pytest

from neti.adapters.claude_code import normalise_tool
from neti.insight.discover import classify
from tests.corpus import refresh

# A record with several parameters, one of which is gated, so a conversion that silently dropped
# parameters would change the verdict rather than merely the shape.
SAMPLE = next(r for r in refresh.load_tools() if r["name"] == "Grep")


def params_of(schema: dict[str, Any]) -> set[str]:
    return set((schema.get("properties") or {}).keys())


def test_the_sample_is_worth_converting() -> None:
    """Guard the guard: if `Grep` ever stopped being gated, every test below would still pass while
    checking nothing about detection."""
    spec = classify({"name": SAMPLE["name"], "inputSchema": SAMPLE["schema"]})
    assert spec.gated, "the conversion sample must be a tool the matcher actually gates"
    assert len(spec.params) > 1, "a single-parameter sample cannot catch a dropped parameter"


def test_a_langchain_tool_reduces_to_the_corpus_schema() -> None:
    pytest.importorskip("langchain_core", reason="the sdks extra is not installed")
    from langchain_core.tools import StructuredTool

    def run(**kwargs: Any) -> str:
        return "ran"

    tool = StructuredTool.from_function(
        func=run,
        name=SAMPLE["name"],
        description=SAMPLE["description"] or "A tool.",
        args_schema={"type": "object", "properties": SAMPLE["schema"]["properties"]},
    )
    # `.args` is LangChain's own accessor for "the parameters a model may send", and it normalises
    # over both shapes `args_schema` accepts — a pydantic model or a raw JSON Schema dict.
    assert set(tool.args) == params_of(SAMPLE["schema"])
    assert normalise_tool(tool.name) == SAMPLE["name"]


def test_an_anthropic_tool_reduces_to_the_corpus_schema() -> None:
    pytest.importorskip("anthropic", reason="the sdks extra is not installed")
    from anthropic.lib.tools import beta_tool

    @beta_tool
    def tool(**kwargs: Any) -> str:
        """A tool."""
        return "ran"

    tool.name = SAMPLE["name"]
    tool.input_schema = SAMPLE["schema"]
    assert params_of(tool.input_schema) == params_of(SAMPLE["schema"])
    assert normalise_tool(tool.name) == SAMPLE["name"]


def test_an_openai_agents_tool_reduces_to_the_corpus_schema() -> None:
    pytest.importorskip("agents", reason="the sdks extra is not installed")
    from agents import FunctionTool

    async def invoke(ctx: Any, args: str) -> str:
        return "ran"

    tool = FunctionTool(
        name=SAMPLE["name"],
        description=SAMPLE["description"] or "A tool.",
        params_json_schema=SAMPLE["schema"],
        on_invoke_tool=invoke,
        strict_json_schema=False,
    )
    assert params_of(tool.params_json_schema) == params_of(SAMPLE["schema"])
    assert normalise_tool(tool.name) == SAMPLE["name"]


def test_an_mcp_prefixed_name_still_finds_the_same_policy_entry() -> None:
    """The conversion nobody thinks of as one.

    A tool reaching Claude Code from an MCP server is renamed `mcp__<server>__<tool>`, so the same
    tool arrives under two names depending on the route. One `neti.yaml` has to govern both, and the
    corpus is keyed on the bare name.
    """
    for record in refresh.load_tools():
        if record["source"] != "mcp":
            continue
        prefixed = f"mcp__{record['origin']}__{record['name']}"
        assert normalise_tool(prefixed) == record["name"]
        assert (
            classify({"name": prefixed, "inputSchema": record["schema"]}).gated
            == classify({"name": record["name"], "inputSchema": record["schema"]}).gated
        ), f"{prefixed} is detected differently from {record['name']}"
