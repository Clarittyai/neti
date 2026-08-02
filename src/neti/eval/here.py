"""`neti demo --here` — the same lifecycle, pointed at the evaluator's own machine.

The synthetic demo next door is careful to say what it is: *"It demonstrates behaviour, not a
finding."* That is honest and it is also the reason it cannot answer the only question an evaluator
actually asks, which is **"what would this find in *my* environment?"**

This does. Every number below comes off the machine it runs on, through the same `Engine`, the same
`decide`, the same records and the same `report`/`propose` as production — there is no second code
path, for the reason `eval/scenarios.py` states next door: a demo with its own execution path stops
being evidence the moment the two diverge, and they always diverge.

**Six acts, and each says what it proves.** The distinction that matters most is between acts 2 and
3. Act 2 is a measurement of this machine with no traffic at all — a finding, full stop. Act 3
replays a *captured session's shape* against these files: the magnitudes are the evaluator's, the
sequence of calls is somebody else's. Both halves of that sentence have to survive into the output,
because the demo's whole value is that a sceptical reader can check it.

Acts 1 and 2 need nothing but the directory they are standing in. The rest need traffic, and when
there is none the demo says so and prints how to get it — which is not a degraded path, it is
the observe-first advice the product gives anyway.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from neti.config.policy import Policy, load_policy
from neti.core.record import verify_chain
from neti.core.types import ProposedCall
from neti.core.units import may_allow
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.corpus import Corpus
from neti.insight.inventory import InventoryRow, build_inventory
from neti.insight.propose import propose
from neti.insight.replay import replay
from neti.insight.report import ReportSummary, build_report
from neti.resolvers.base import ResolveContext
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from neti.store.jsonl import JsonlSink, read_records

__all__ = ["HERE_DISCLAIMER", "Finding", "HereResult", "run_here"]

HERE_DISCLAIMER = (
    "Measured on this machine. The magnitudes are yours — every one was produced by walking these "
    "files, through the same decision path the gate uses in production. The *sequence* of calls in "
    "act 3 is a captured session's, not yours: the numbers are real, the story is borrowed."
)
"""The sentence that has to survive a hostile read.

Its synthetic sibling in `demo.py` says "demonstrates behaviour, not a finding". This one is
allowed to claim a finding, so it has to be exact about which half is measured and which half is
borrowed. Overstating here would discredit every honest number next to it.
"""


@dataclass
class Finding:
    """The one paragraph an evaluator pastes into Slack."""

    headline: str
    detail: str = ""


@dataclass
class HereResult:
    root: Path
    disclaimer: str = HERE_DISCLAIMER
    servers: list[dict[str, str]] = field(default_factory=list)
    already_gated: list[str] = field(default_factory=list)
    reach: list[InventoryRow] = field(default_factory=list)

    observed: Counter[str] = field(default_factory=Counter)
    corpus_size: int = 0
    unresolved: int = 0
    """Corpus targets that do not exist here. Counted rather than averaged away — a corpus that
    mostly misses is a poor fit for this repository and the demo should say so."""

    report: ReportSummary | None = None
    proposals: list[Any] = field(default_factory=list)
    enforced: Counter[str] = field(default_factory=Counter)
    blocked_examples: list[str] = field(default_factory=list)

    records: int = 0
    chain_ok: bool = False
    replayed: int = 0

    findings: list[Finding] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    @property
    def has_traffic(self) -> bool:
        return self.corpus_size > 0


def _engine(policy: Policy, mode: Mode) -> Engine:
    """Always the real one. `--here` never touches the synthetic tenant, so the Entra resolvers are
    present but unreachable — a policy that named one would fail loudly on its first token fetch."""
    blank = ClientCredential(tenant_id="", client_id="", client_secret="")
    client = GraphClient(blank)
    return Engine(
        policy=policy.model_copy(update={"mode": mode}),
        resolvers=resolvers_for_client(client, policy.providers),
        ctx=ResolveContext(),
    )


# ---------------------------------------------------------------------------- act 1


def _discover(root: Path) -> tuple[list[dict[str, str]], list[str]]:
    """What agents this machine is configured to run, read from the configs they already have."""
    from neti.insight.discover import find_clients

    gated: list[str] = []
    try:
        servers = find_clients(cwd=root, already_gated=gated)
    except Exception:  # discovery is a nicety; a malformed config must not end the demo
        return [], gated
    return [
        {"name": s.name, "client": s.client, "command": " ".join(s.argv)[:60]} for s in servers
    ], gated


# ---------------------------------------------------------------------------- the run


def run_here(
    root: Path,
    policy_path: str | Path,
    *,
    corpus: Corpus | None = None,
    records_dir: Path | None = None,
    policy_override: Policy | None = None,
) -> HereResult:
    """The whole lifecycle against `root`, returning what to print.

    `policy_path` is the shipped `examples/coding-agent.yaml` unless the evaluator has their own.
    Its `providers.fs.root` is overridden to `root`, because a demo that measured the directory the
    policy happened to name rather than the one it was pointed at would be measuring nothing.
    """
    root = root.resolve()
    base = policy_override if policy_override is not None else load_policy(policy_path)
    providers = {**base.providers, "fs": {**(base.providers.get("fs") or {}), "root": str(root)}}
    policy = base.model_copy(update={"providers": providers})

    result = HereResult(root=root)
    result.servers, result.already_gated = _discover(root)

    # ---------------------------------------------------------------- act 2: reach
    observing = _engine(policy, Mode.OBSERVE)
    result.reach = build_inventory(policy, observing.resolvers, ResolveContext())
    biggest = max(
        (r for r in result.reach if r.reachable.magnitude is not None),
        key=lambda r: r.reachable.magnitude or 0,
        default=None,
    )
    if biggest is not None and biggest.reachable.magnitude:
        binding = sum(
            1
            for r in result.reach
            if r.resolver == biggest.resolver and r.reachable.magnitude is not None
        )
        # "at least", when the walk stopped at its cap. Measured on a 712,359-file tree this said
        # "reaches 200,000 objects" — the cap, presented as the answer, understating the truth by
        # 3.5x. A capped count is a floor and the sentence has to carry that, both because it is
        # what the resolver reported and because the floor is the more alarming number anyway:
        # "at least 200,000, and we stopped counting" is the honest version and the stronger one.
        floor = not may_allow(biggest.reachable.direction)
        amount = (
            f"at least {biggest.reachable.magnitude:,}"
            if floor
            else f"{biggest.reachable.magnitude:,}"
        )
        result.findings.append(
            Finding(
                headline=(
                    f"An agent working here reaches {amount} "
                    f"{biggest.reachable.unit.value}, across {binding} gated parameter(s)."
                ),
                # Deliberately *not* "in a single X call". Reachable-max is a property of the
                # resolver and the root, not of any one tool — the first draft of this line said
                # "in a single Edit call", which is flatly false: `Edit` takes one `file_path` and
                # touches one file. The tool that happened to sort first was being credited with
                # the whole tree. An overstated headline discredits every honest number beneath it.
                detail=(
                    (
                        "The walk stopped at its cap, so that is a floor rather than a total. "
                        if floor
                        else ""
                    )
                    + "It bounds what one credential can address here; it does not measure any "
                    "single call. Nothing in a permission system reports either number — it "
                    "answers whether, not how many."
                ),
            )
        )

    if corpus is None or not len(corpus):
        result.next_steps = [
            "Install the hook and work normally for an afternoon:",
            '  {"hooks": {"PreToolUse": [{"matcher": "*",',
            '    "hooks": [{"type": "command", "command": "neti hook -c neti.yaml"}]}]}}',
            "Then run this again — acts 3 to 6 need traffic, and that is how you get it.",
        ]
        return result

    # ---------------------------------------------------------------- act 3: observe
    records_dir = records_dir or (root / ".neti")
    observed_path = records_dir / "observed.ndjson"
    result.observed, result.unresolved = _run_corpus(observing, corpus, root, observed_path)
    result.corpus_size = len(corpus)

    # ---------------------------------------------------------------- act 4: report + propose
    result.report = build_report(read_records(observed_path))
    result.proposals = [p for p in propose(result.report) if p.actionable]

    # ---------------------------------------------------------------- act 5: enforce
    if result.proposals:
        tightened = _with_proposed_bands(policy, result.proposals)
        enforced_path = records_dir / "enforced.ndjson"
        enforcing = _engine(tightened, Mode.ENFORCE)
        result.enforced, _ = _run_corpus(enforcing, corpus, root, enforced_path)
        result.blocked_examples = _blocked_examples(enforced_path)
        if result.enforced.get("block"):
            result.findings.append(
                Finding(
                    headline=(
                        f"Under ceilings derived from that traffic, "
                        f"{result.enforced['block']:,} of {len(corpus):,} calls would have been "
                        "stopped before they ran."
                    ),
                    detail="Every one of them was a call the agent was permitted to make.",
                )
            )

    # ---------------------------------------------------------------- act 6: audit
    chain = list(read_records(observed_path))
    result.records = len(chain)
    result.chain_ok, _ = verify_chain(chain)
    result.replayed = replay(chain, policy.model_copy(update={"mode": Mode.OBSERVE})).replayed
    return result


def _run_corpus(
    engine: Engine, corpus: Corpus, root: Path, records: Path
) -> tuple[Counter[str], int]:
    """Re-run every captured call against these files.

    Re-run, not re-derive: see the module docstring for why those words are kept apart.
    """
    records.parent.mkdir(parents=True, exist_ok=True)
    if records.exists():
        records.unlink()
    sink = JsonlSink(records)
    counts: Counter[str] = Counter()
    missing = 0

    for call in corpus.calls:
        if not (root / call.target).exists():
            missing += 1
        result = engine.gate(ProposedCall(tool=call.tool, args=call.args(root)))
        sink.write(result.record)
        counts[result.record.verdict] += 1
    return counts, missing


def _with_proposed_bands(policy: Policy, proposals: list[Any]) -> Policy:
    """Apply what `propose` suggested, the way the operator is told to: merge the bands into the
    gates that already exist, keeping every `resolver:` line."""
    # Dumped, edited and revalidated rather than `model_copy(update=...)`. `model_copy` does not
    # re-run validation, so handing it raw dicts leaves `policy.tools` full of dicts that only fail
    # later, deep inside the engine, with `'dict' object has no attribute 'gate'`.
    raw = policy.model_dump(mode="json")
    for p in proposals:
        gate = raw["tools"].get(p.tool, {}).get("gate", {})
        if p.pointer not in gate:
            continue
        gate[p.pointer]["bands"] = [
            {"above": p.confirm_above, "verdict": "confirm"},
            {"above": p.block_above, "verdict": "block"},
        ]
    return Policy.model_validate(raw)


def _blocked_examples(records: Path, limit: int = 3) -> list[str]:
    out: list[str] = []
    for record in read_records(records):
        if record.verdict != "block":
            continue
        for cause in record.causes:
            if cause.get("magnitude") is None:
                continue
            target = str(cause.get("target") or "")
            out.append(
                f"{record.tool}({Path(target).name or target}) → "
                f"{int(cause['magnitude']):,} {cause['unit']}"
            )
            break
        if len(out) >= limit:
            break
    return out
