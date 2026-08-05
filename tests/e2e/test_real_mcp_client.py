"""A real MCP *client* driving `neti gate`, over a real pipe.

`test_real_mcp_server.py` covers the other direction: neti's gateway in front of a real MCP server.
The client in that test is always ours. This one inverts it — the official `mcp` SDK's
`ClientSession` connects to `neti gate --stdio`, exactly the way an editor or a CLI agent does,
because that is the seam every runtime without a Python adapter arrives through.

**It is the widest claim this project makes and it was the least tested.** `README.md` names Cursor,
Claude Desktop, Windsurf, Cline, Continue, VS Code, Zed and Goose as reached without an adapter, on
the reasoning that they speak MCP and the gate goes in front of the server. That reasoning is
sound and it rested on nobody ever having run a third-party client against the gate at all.

What this drives is the SDK those clients are built on, at the version they ship. It is deliberately
*not* claimed to be a test of any particular product's user interface: OpenClaw bundles
`@modelcontextprotocol/sdk` 1.29.0 and OpenAI's Codex CLI speaks the same protocol, and what is
verified here is that a client speaking it is gated — not that their menus work. The distinction is
kept because `neti score` already keeps it: an adapter row is *driven*, an MCP client is *reached*,
and the card must not blur the two.

The child server answers every call, so a gate that forwarded a call it should have stopped is
caught by the child *replying* rather than by an assertion somebody remembered to write.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="the official MCP SDK is not installed")

POLICY = """\
version: 1
mode: enforce
unknown_tool: allow

providers:
  fs:
    root: {root}

tools:
  Glob:
    gate:
      /pattern:
        resolver: fs.paths
        bands:
          - {{ above: 10, verdict: block }}
        on_unresolved: block
"""

# Answers anything, and records what it was asked. If the gate ever forwards a blocked call, the
# tool list below stops being the only thing this child was used for and the assertion fails on
# real behaviour rather than on a mock's bookkeeping.
SERVER = """
import asyncio, json, sys
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

app = Server("child")

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [types.Tool(
        name="Glob",
        description="Match files.",
        inputSchema={"type": "object", "properties": {"pattern": {"type": "string"}}},
    )]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    return [types.TextContent(type="text", text="THE CHILD RAN THE TOOL")]

async def main() -> None:
    async with stdio_server() as (r, w):
        await app.run(r, w, app.create_initialization_options())

asyncio.run(main())
"""


def _tree(root: Path, count: int = 30) -> Path:
    tree = root / "tree"
    tree.mkdir()
    for index in range(count):
        (tree / f"f{index}.txt").write_text("x\n", encoding="utf-8")
    return tree


async def _drive(project: Path, pattern: str) -> tuple[str, list[str]]:
    """Connect the SDK's own client to `neti gate --stdio` and make one oversized call."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "neti.cli",
            "gate",
            "--stdio",
            "-c",
            "neti.yaml",
            "-r",
            "out/decisions.ndjson",
            "--",
            sys.executable,
            "-c",
            SERVER,
        ],
        cwd=str(project),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        names = [tool.name for tool in listed.tools]
        result = await session.call_tool("Glob", {"pattern": pattern})
        text = "".join(
            getattr(block, "text", "") for block in result.content if hasattr(block, "text")
        )
        return text, names


def test_a_real_mcp_client_is_gated_by_neti_gate(tmp_path: Path) -> None:
    """The whole seam, end to end: third-party client, real pipe, real handshake, real refusal."""
    import anyio

    project = tmp_path / "project"
    project.mkdir()
    tree = _tree(tmp_path)
    (project / "neti.yaml").write_text(POLICY.format(root=tree), encoding="utf-8")

    text, names = anyio.run(_drive, project, f"{tree}/*.txt")

    assert names == ["Glob"], (
        "the gate did not relay the server's tool list unchanged — a client must not be able to "
        f"tell a gated server from an ungated one by looking at it, but it saw {names}"
    )
    assert "THE CHILD RAN THE TOOL" not in text, (
        "the gate forwarded a call it should have stopped: the upstream server actually ran it"
    )
    # The sentence, not the digits. A bare `"30" in text` would pass on a temp path that happened
    # to contain those characters, which is exactly the kind of assertion that survives a
    # regression.
    assert "resolves to 30 objects" in text, (
        "the client was refused without being told the magnitude, which is the number that lets "
        f"an agent narrow its target. It got: {text!r}"
    )

    records = project / "out" / "decisions.ndjson"
    assert records.exists(), "the gate refused the call and recorded nothing"
    last = json.loads(records.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["verdict"] == "block"
    assert last["causes"][0]["magnitude"] == 30


def test_the_gate_is_invisible_to_a_client_when_the_call_fits(tmp_path: Path) -> None:
    """The other half, and the one that decides whether anybody leaves this installed.

    A gate that stops oversized calls but also breaks ordinary ones is a gate people remove. The
    same client, the same server, a pattern under the ceiling: the child's answer comes back
    untouched.
    """
    import anyio

    project = tmp_path / "project"
    project.mkdir()
    tree = _tree(tmp_path)
    (project / "neti.yaml").write_text(POLICY.format(root=tree), encoding="utf-8")

    text, _ = anyio.run(_drive, project, f"{tree}/f0.txt")
    assert "THE CHILD RAN THE TOOL" in text, (
        f"a call well under the ceiling did not reach the server. The client got: {text!r}"
    )
