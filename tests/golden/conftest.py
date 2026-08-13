"""Fixtures for the golden transcripts. See `test_golden.py` for what they are for."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "entra.yaml"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty project with a policy, isolated from the developer's own machine.

    `NETI_HOME` is redirected so a developer who happens to be logged in to a control plane does not
    get different transcripts from CI — the golden files would then differ per machine and the whole
    mechanism would be abandoned within a week.

    The home *directory* is redirected for the same reason, and it was the half this fixture
    forgot. `neti init` reads the machine's agent-client configs, and `find_clients` takes a `home`
    argument only so a test can point it somewhere — the CLI never passes one, so it falls through
    to `Path.home()`. Two transcripts therefore pinned "No MCP servers found in any client config on
    this machine", which was a statement about the author's laptop on the day they were written.
    Both began failing the moment somebody registered an MCP server in Claude Code, and they would
    fail for any contributor who has ever configured one — which is most of the people this project
    wants patches from, and a failure they could do nothing about.

    `Path.home()` reads `$HOME` on POSIX and `USERPROFILE` on Windows, so setting both is what makes
    this hold on every machine CI runs on rather than the one it was written on.
    """
    machine = tmp_path / "machine"
    machine.mkdir()
    monkeypatch.setenv("HOME", str(machine))
    monkeypatch.setenv("USERPROFILE", str(machine))
    monkeypatch.setenv("NETI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("NETI_CLOUD_URL", raising=False)
    monkeypatch.delenv("NETI_CLOUD_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "neti.yaml").write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def write_records(path: Path, magnitudes: list[int], *, tool: str = "send_email") -> Path:
    """A records file with the given magnitudes, gated for real so the shapes are genuine."""
    from neti.config.policy import load_policy
    from neti.core.types import ProposedCall
    from neti.core.verdict import Mode
    from neti.engine import Engine
    from neti.eval.synthetic import Group, SyntheticTenant
    from neti.resolvers.graph_client import ClientCredential, GraphClient
    from neti.resolvers.registry import resolvers_for_client
    from neti.store.jsonl import JsonlSink

    groups = {
        f"g-{m}": Group(
            group_id=f"g-{m}", display_name=f"Group {m}", transitive_members=m, app_assignments=1
        )
        for m in sorted(set(magnitudes))
    }
    tenant = SyntheticTenant(groups=groups)
    client = GraphClient(ClientCredential("d", "d", "d"), transport=tenant.transport())
    policy = load_policy(EXAMPLE).model_copy(update={"mode": Mode.OBSERVE})
    engine = Engine(policy=policy, resolvers=resolvers_for_client(client))
    sink = JsonlSink(path)
    try:
        for m in magnitudes:
            sink.write(engine.gate(ProposedCall(tool=tool, args={"to": f"g-{m}"})).record)
    finally:
        sink.close()
        client.close()
    return path


def break_chain(path: Path) -> Path:
    """Tamper with a stored magnitude, leaving the digest that no longer covers it."""
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if rows and rows[0]["causes"]:
        rows[0]["causes"][0]["magnitude"] = 1
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path
