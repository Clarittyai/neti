"""`neti gate --stdio` in front of a real MCP server, over a real pipe.

Everything else in the suite drives the stdio transport against a fake written for the occasion.
This drives it against `@modelcontextprotocol/server-filesystem`, launched exactly the way Claude
Code, Claude Desktop and Cursor launch it — `npx -y <package> <dir>` — because stdio is the
transport essentially every local MCP server actually uses, and the failures it has are the ones a
purpose-built fake cannot have:

- a real handshake, with a `protocolVersion` and capabilities we did not choose
- a real tool schema, with argument names we did not choose (`path`, not `target`)
- **a real stderr banner.** This server prints "Secure MCP Filesystem Server running on stdio" to
  stderr on startup. Relaying a server's stderr while gating it is correct — an operator needs to
  see why their server failed — but a byte of it on *stdout* corrupts the JSON-RPC stream, and
  `neti init` once made a working scan look broken by interleaving exactly this kind of noise.

Skipped when `npx` is absent, so a fresh clone stays offline; CI installs Node so it runs there.
The first run downloads the package, which is why the timeout is generous.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SERVER_PACKAGE = "@modelcontextprotocol/server-filesystem"

# Resolved to an absolute path rather than passed as the bare name. On Windows `npx` is
# `npx.cmd`, and `subprocess` with a bare "npx" raises FileNotFoundError — CI runs windows-latest,
# so a test written the obvious way would have gone red there and nowhere else.
NPX = shutil.which("npx")

pytestmark = pytest.mark.skipif(NPX is None, reason="needs Node/npx to run a real MCP server")

POLICY = """\
version: 1
mode: enforce
unknown_tool: allow

tools:
  directory_tree:
    gate:
      /path:
        resolver: fs.paths
        bands:
          - {{ above: {ceiling}, verdict: block }}
        on_unresolved: block
        on_unbounded: confirm
  list_directory:
    gate:
      /path:
        resolver: fs.paths
        bands:
          - {{ above: {ceiling}, verdict: block }}
        on_unresolved: block
"""


def rpc(id_: int, method: str, params: dict[str, Any] | None = None) -> str:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message)


HANDSHAKE = rpc(
    1,
    "initialize",
    {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "neti-e2e", "version": "1"},
    },
)


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A directory with a small subtree and a large one, so one call fits and one does not."""
    root = tmp_path_factory.mktemp("served")
    (root / "small").mkdir()
    for i in range(3):
        (root / "small" / f"f{i}.txt").write_text("x", encoding="utf-8")
    (root / "large").mkdir()
    for i in range(80):
        (root / "large" / f"f{i}.txt").write_text("x", encoding="utf-8")
    return root


def gate(tree: Path, policy: Path, lines: list[str], timeout: int = 300) -> dict[str, Any]:
    """Run the gate in front of the real server and return `{id: response}`."""
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "neti.cli",
            "gate",
            "--stdio",
            "--config",
            str(policy),
            "--demo",
            "--",
            str(NPX),
            "-y",
            SERVER_PACKAGE,
            str(tree),
        ],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        timeout=timeout,
        # `npx` needs a real PATH and HOME to find or fetch the package.
        env={**os.environ},
    )
    assert out.returncode == 0, f"the gate exited {out.returncode}\n{out.stderr[-2000:]}"

    responses: dict[str, Any] = {}
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        # Every line on stdout must be JSON-RPC. This is the assertion the stderr banner exists to
        # threaten: one byte of server chatter here and a real client's parser gives up.
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover - the failure path is the point
            pytest.fail(f"non-JSON on stdout, which corrupts the stream: {line[:200]!r} ({exc})")
        if "id" in message:
            responses[str(message["id"])] = message
    return responses


@pytest.fixture(scope="module")
def policy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("policy") / "neti.yaml"
    target.write_text(POLICY.format(ceiling=10), encoding="utf-8")
    return target


def test_the_handshake_and_tool_list_survive_the_gate(tree: Path, policy: Path) -> None:
    """Non-tool traffic passes through untouched. A gate that broke `initialize` would be a gate
    nobody could install, and `tools/list` is how the model learns what it may call at all."""
    responses = gate(tree, policy, [HANDSHAKE, rpc(2, "tools/list")])

    assert responses["1"]["result"]["protocolVersion"], "the real server's handshake must survive"
    assert "serverInfo" in responses["1"]["result"]

    tools = {t["name"] for t in responses["2"]["result"]["tools"]}
    assert {"directory_tree", "list_directory", "read_file"} <= tools
    assert responses["2"]["result"]["tools"], "the schema the model sees must be the server's own"


def test_an_under_ceiling_call_reaches_the_server_and_returns_its_real_output(
    tree: Path, policy: Path
) -> None:
    """The half that is easy to get wrong in the safe direction. A gate that blocked everything
    would pass every denial test in this file."""
    responses = gate(
        tree,
        policy,
        [
            HANDSHAKE,
            rpc(
                3,
                "tools/call",
                {"name": "list_directory", "arguments": {"path": str(tree / "small")}},
            ),
        ],
    )
    result = responses["3"]["result"]
    assert not result.get("isError"), f"a call that fits was stopped: {result}"
    text = json.dumps(result)
    assert "f0.txt" in text, "the server's own answer must come back, not something we invented"


def test_an_over_ceiling_call_is_denied_as_a_tool_result(tree: Path, policy: Path) -> None:
    """A denial is a tool result with `isError`, never a protocol error.

    A protocol error would be retried or would surface as a broken client; a tool result carrying
    the number is read by the model, which is what makes it narrow its scope and try again.
    """
    responses = gate(
        tree,
        policy,
        [
            HANDSHAKE,
            rpc(
                4,
                "tools/call",
                {"name": "directory_tree", "arguments": {"path": str(tree / "large")}},
            ),
        ],
    )
    message = responses["4"]

    assert "error" not in message, "a denial must not be a JSON-RPC protocol error"
    result = message["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "80" in text, f"the denial must name the magnitude: {text}"
    assert "ceiling of 10" in text


def test_the_server_never_sees_a_blocked_call(tree: Path, policy: Path) -> None:
    """The claim the whole product rests on, checked against a server that would have complied.

    `directory_tree` on the large directory succeeds if it reaches the server, so the only reason
    a denial can come back is that the gate stopped it first.
    """
    allowed = gate(
        tree,
        policy,
        [
            HANDSHAKE,
            rpc(
                5,
                "tools/call",
                {"name": "directory_tree", "arguments": {"path": str(tree / "small")}},
            ),
        ],
    )
    assert not allowed["5"]["result"].get("isError"), "the server does answer this call"

    denied = gate(
        tree,
        policy,
        [
            HANDSHAKE,
            rpc(
                6,
                "tools/call",
                {"name": "directory_tree", "arguments": {"path": str(tree / "large")}},
            ),
        ],
    )
    assert denied["6"]["result"]["isError"] is True


def test_an_ungated_tool_passes_straight_through(tree: Path, policy: Path) -> None:
    """`unknown_tool: allow` is deliberate — an ungated tool is out of scope, not denied — and this
    is the version of that claim measured against a real server's real tool."""
    responses = gate(
        tree,
        policy,
        [HANDSHAKE, rpc(7, "tools/call", {"name": "list_allowed_directories", "arguments": {}})],
    )
    assert not responses["7"]["result"].get("isError")


def test_the_servers_stderr_banner_does_not_corrupt_the_stream(tree: Path, policy: Path) -> None:
    """This server greets stderr on startup with "Secure MCP Filesystem Server running on stdio".

    `gate` above already fails on any non-JSON line, so reaching this assertion at all is the
    result. Kept as its own test because the reason is worth naming: relaying a server's stderr is
    correct and necessary, and putting one byte of it on stdout breaks every client.
    """
    responses = gate(tree, policy, [HANDSHAKE, rpc(8, "tools/list")])
    assert set(responses) == {"1", "8"}, "exactly the responses asked for, and nothing else"
