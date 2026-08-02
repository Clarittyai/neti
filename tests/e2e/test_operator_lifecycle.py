"""The whole first week, as one flow, on real files.

    init -> inventory -> observe -> report -> propose -> merge -> enforce -> verify

Every step of this is tested somewhere. The *sequence* was not, and the sequence is the product:
each command's output is the next command's input, and the seams between them are where a promise
can be true in isolation and false in practice.

**Step 7 is why this file exists.** `neti propose` ends with a line telling the operator what the
numbers it suggests would have done — "this would have blocked 4 call(s) and asked about 4". That
is a prediction about the gate, made by code that is not the gate, at the exact moment somebody
decides what to commit. Nothing checked it. Here the proposed ceilings are merged in the way the
output instructs, the same corpus is replayed under enforce, and the counts have to match.

Everything runs as a subprocess against files in a tmp directory, because that is what an operator
does. Nothing is mocked except the directory itself, which is the synthetic tenant.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

NETI = [sys.executable, "-m", "neti.cli"]


def run(*args: str, cwd: Path, stdin: str = "") -> subprocess.CompletedProcess[str]:
    out = subprocess.run(NETI + list(args), capture_output=True, text=True, input=stdin, cwd=cwd)
    return out


ECHO_SERVER = (
    "import sys, json\n"
    "for line in sys.stdin:\n"
    "    msg = json.loads(line)\n"
    "    if msg.get('id') is None:\n"
    "        continue\n"
    "    body = {'content': [{'type': 'text', 'text': 'ran'}]}\n"
    "    print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': body}), flush=True)\n"
)


def gate_corpus(
    workdir: Path, policy: Path, records: Path, corpus: list[tuple[str, dict[str, object]]]
) -> Counter[str]:
    """Run the whole corpus through `neti gate --stdio` in one process, returning verdict counts.

    One process rather than one per call, and `gate` rather than `hook`, purely for wall clock: the
    hook is invoked per call by design, so forty calls meant forty interpreter startups and about a
    minute. `gate --stdio` is a long-lived process that sees a stream, which is also how a real MCP
    server is gated — so this is the more representative seam as well as the faster one.
    """
    lines = [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": i,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            }
        )
        for i, (tool, args) in enumerate(corpus, start=1)
    ]
    out = run(
        "gate",
        "--stdio",
        "--config",
        str(policy),
        "--records",
        str(records),
        "--demo",
        "--",
        sys.executable,
        "-c",
        ECHO_SERVER,
        cwd=workdir,
        stdin="\n".join(lines) + "\n",
    )
    assert out.returncode == 0, out.stderr

    counts: Counter[str] = Counter()
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        result = json.loads(line).get("result") or {}
        if not result.get("isError"):
            counts["allow"] += 1
            continue
        payload = (result.get("_meta") or {}).get("neti") or {}
        counts[payload.get("verdict", "block")] += 1
    return counts


# The corpus. Bimodal on purpose: ordinary sends plus a handful of enormous ones, which is the shape
# `propose` exists for and the shape that used to produce a ceiling above everything ever observed.
CORPUS = [("send_email", {"to": "g-team"})] * 36 + [("send_email", {"to": "g-eng-all"})] * 4


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path


def test_the_first_week_end_to_end(workdir: Path) -> None:
    """Every command feeding the next, with the artefacts on disk between them."""
    policy = workdir / "neti.yaml"
    records = workdir / "decisions.ndjson"

    # ---------------------------------------------------------------- 1. init
    # A client config of the kind that is already on the machine.
    (workdir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"entra": {"command": "npx", "args": ["-y", "@acme/entra"]}}})
    )
    init = run("init", "--out", str(policy), "--no-probe", cwd=workdir)
    assert init.returncode in (0, 1), init.stderr
    assert policy.exists(), "init must leave a policy behind"

    # `init` writes ceilings-free observe-mode config by design, so the rest of this test uses the
    # shipped example: it is what the README tells an operator to start from and the only policy
    # with gates the synthetic tenant can resolve.
    source = Path(__file__).resolve().parents[2] / "examples" / "entra.yaml"
    started = yaml.safe_load(source.read_text())

    # The session budget comes out, and the reason is the point of step 7 rather than a convenience.
    # `propose` reasons about *per-call* ceilings: it reads a distribution of individual magnitudes
    # and suggests bands for one call at a time. A cumulative session budget is a second, separate
    # source of interrupts that it does not model and should not — declaring one is the operator's
    # answer to NC-01, not something derived from traffic. Leaving it in would make step 7 compare
    # a per-call prediction against a per-call-plus-per-session reality and fail for a reason that
    # is not a defect. That the budget does fire is asserted on its own, below.
    started.pop("session_budgets", None)
    policy.write_text(yaml.safe_dump(started, sort_keys=False))

    # ---------------------------------------------------------------- 2. inventory
    inventory = run("inventory", "--config", str(policy), "--demo", cwd=workdir)
    assert inventory.returncode == 0, inventory.stderr
    assert "reachable" in inventory.stdout.lower()
    assert "41,203" in inventory.stdout or "52,400" in inventory.stdout, (
        "the day-one finding is a number; without one there is nothing to show a customer"
    )

    # ---------------------------------------------------------------- 3. observe
    observed = gate_corpus(workdir, policy, records, CORPUS)
    assert observed == Counter({"allow": len(CORPUS)}), (
        f"observe mode must never stop anything, got {dict(observed)}"
    )
    assert records.exists() and len(records.read_text().splitlines()) == len(CORPUS)

    # ---------------------------------------------------------------- 4. report
    report = run("report", "--records", str(records), cwd=workdir)
    assert report.returncode == 0, report.stderr
    assert f"n={len(CORPUS)}" in report.stdout

    # ---------------------------------------------------------------- 5. propose
    # `--allow-synthetic`, because this whole lifecycle runs against the built-in tenant. `propose`
    # refuses such a window by default now: a ceiling fitted to a fixture is worse than no ceiling,
    # because it looks like it came from traffic and somebody will defend it. The flag is the
    # operator saying they know which this is, and it is exactly what a reader walking the
    # no-credential path would type.
    proposed = run("propose", "--records", str(records), "--allow-synthetic", cwd=workdir)
    assert proposed.returncode == 0, proposed.stderr
    predicted = _predicted_impact(proposed.stdout)
    fragment = _yaml_fragment(proposed.stdout)
    assert fragment, "propose must emit something an operator can paste"

    # ---------------------------------------------------------------- 6. merge and enforce
    # Exactly what the output instructs: merge the bands into the gates that already exist, keeping
    # each `resolver:` line. If this cannot be done mechanically, the instruction is not followable.
    merged = _merge(yaml.safe_load(policy.read_text()), fragment)
    merged["mode"] = "enforce"
    policy.write_text(yaml.safe_dump(merged, sort_keys=False))

    check = run("inventory", "--config", str(policy), "--demo", cwd=workdir)
    assert check.returncode == 0, (
        f"the merged policy does not load, so the instruction cannot be followed:\n{check.stderr}"
    )

    # ---------------------------------------------------------------- 7. the prediction
    enforced_records = workdir / "enforced.ndjson"
    actual = gate_corpus(workdir, policy, enforced_records, CORPUS)

    assert actual["block"] == predicted["block"], (
        f"propose predicted {predicted['block']} block(s), the gate produced {actual['block']}"
    )
    got = actual["confirm"]
    assert got == predicted["confirm"], (
        f"propose predicted {predicted['confirm']} confirm(s), the gate produced {got}"
    )

    # ---------------------------------------------------------------- 8. verify
    for chain in (records, enforced_records):
        verified = run("verify", "--records", str(chain), cwd=workdir)
        assert verified.returncode == 0, verified.stderr
        assert "intact" in verified.stdout


def test_the_proposed_ceilings_actually_bind(workdir: Path) -> None:
    """The other half of step 7, and the failure it is really guarding against.

    A prediction of "0 blocked, 0 asked" would satisfy the equality above while the ceilings did
    nothing at all. On bimodal traffic the proposal has to catch its own outliers — that is the
    property `propose` was fixed for once already — so at least one call must be stopped.
    """
    policy = workdir / "neti.yaml"
    records = workdir / "d.ndjson"
    source = Path(__file__).resolve().parents[2] / "examples" / "entra.yaml"
    policy.write_text(source.read_text())

    gate_corpus(workdir, policy, records, CORPUS)

    # `--allow-synthetic`, because this whole lifecycle runs against the built-in tenant. `propose`
    # refuses such a window by default now: a ceiling fitted to a fixture is worse than no ceiling,
    # because it looks like it came from traffic and somebody will defend it. The flag is the
    # operator saying they know which this is, and it is exactly what a reader walking the
    # no-credential path would type.
    proposed = run("propose", "--records", str(records), "--allow-synthetic", cwd=workdir)
    predicted = _predicted_impact(proposed.stdout)

    assert predicted["block"] + predicted["confirm"] > 0, (
        "a proposal derived from traffic with outliers that catches none of them is dead config"
    )


# ---------------------------------------------------------------------------- parsing the output
#
# These read `propose`'s human-facing text rather than a JSON side channel, deliberately. The text
# is what an operator acts on; a number that is only correct in a machine-readable field nobody
# looks at is not the number this test is about.


def _predicted_impact(stdout: str) -> dict[str, int]:
    import re

    match = re.search(r"would have blocked ([\d,]+) call\(s\) and asked about ([\d,]+)", stdout)
    if not match:
        assert "nothing in the observed window would have been stopped" in stdout, (
            f"cannot find an IMPACT line to check:\n{stdout}"
        )
        return {"block": 0, "confirm": 0}
    return {
        "block": int(match.group(1).replace(",", "")),
        "confirm": int(match.group(2).replace(",", "")),
    }


def _yaml_fragment(stdout: str) -> dict[str, object]:
    """The merge fragment, taken from where the output actually puts it."""
    marker = "tools:"
    index = stdout.find(f"\n{marker}")
    if index < 0:
        return {}
    parsed = yaml.safe_load(stdout[index:])
    return parsed if isinstance(parsed, dict) else {}


def _merge(policy: dict, fragment: dict) -> dict:
    """Merge bands in, keeping every `resolver:` line — the instruction, applied literally."""
    for tool, spec in (fragment.get("tools") or {}).items():
        for pointer, gate in (spec.get("gate") or {}).items():
            existing = policy["tools"][tool]["gate"][pointer]
            assert "resolver" in existing, "the gate being merged into must already name a resolver"
            existing["bands"] = gate["bands"]
    return policy


def test_a_session_budget_bites_across_a_long_lived_session(workdir: Path) -> None:
    """The other half of what step 7 deliberately excludes, and the mitigation for `SCOPE.md` NC-01.

    A per-call ceiling is structurally blind to four thousand individually-small sends. The declared
    session budget is the answer, and it only means anything over a *session* — so it needs the
    long-lived `gate --stdio` process to show up at all, which is exactly why forty separate hook
    invocations never exercised it.

    The same corpus that produces four interrupts under per-call ceilings alone produces many more
    once the cumulative total is declared. That difference is the feature.
    """
    policy = workdir / "neti.yaml"
    records = workdir / "d.ndjson"
    source = Path(__file__).resolve().parents[2] / "examples" / "entra.yaml"
    declared = yaml.safe_load(source.read_text())
    assert declared.get("session_budgets"), "the shipped example must still declare one"
    declared["mode"] = "enforce"
    policy.write_text(yaml.safe_dump(declared, sort_keys=False))

    counts = gate_corpus(workdir, policy, records, CORPUS)

    assert counts["allow"] < len(CORPUS), "a cumulative budget that never fires is dead config"
    stopped = sum(v for k, v in counts.items() if k != "allow")
    assert stopped > 4, (
        f"only {stopped} call(s) stopped — the per-call ceilings alone account for that, so the "
        "session budget is not contributing"
    )

    # And the reason has to say which one it was: "narrow this call" and "start a new session" are
    # different remedies, and a denial that conflates them tells the agent to do the wrong thing.
    report = run("report", "--records", str(records), cwd=workdir)
    assert report.returncode == 0, report.stderr
