"""M12 — does asking a model actually help, and how often is it wrong?

`neti suggest` is only worth shipping if a model can do this job. That is a question with an answer,
not an opinion, and this is where the answer gets produced.

**Arm A, recovery, is the go/no-go and needs no hand-labelling at all.** `tests/corpus/` holds 170
real tool schemas and a committed judgement on every parameter of every one. Thirty-one of those
tools are gated by the rule table. This arm feeds those thirty-one back with *every* parameter
marked eligible — the rule table's answer withheld — and scores what comes back against the
committed key:

    recovered        right pointer, right resolver
    wrong_resolver   right pointer, wrong resolver
    missed           the rule table gates it, the model said not_a_set
    extra            claimed a parameter the rule table declined on that tool

If a model cannot recover gates the rules already make on tools it can see, nothing downstream is
interpretable and the honest thing is to publish that number and stop. It costs one run.

**Arm B, over-claim**, is the instrument for the failure this whole design is arranged around. The
41 parameters the rule table declined *with a written reason* — `query` on a search server, `owner`
next to `repo` — are sent deliberately here, and every claim is an over-claim by construction, so
the rate is measurable. Eval only: `neti suggest` structurally cannot send these.

Arm C, the 401 unclaimed, needs adjudication and is not automatic. It is deliberately not in this
file yet: arms A and B decide whether it is worth labelling anything.

    ANTHROPIC_API_KEY=... just assist

Never in CI. It costs tokens, it is not deterministic, and the key belongs to whoever runs it.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "tests" / "corpus" / "decisions.json"
RESULTS = REPO / "eval" / "results" / "assist_recovery.json"


@dataclass
class Arm:
    of: int = 0
    recovered: int = 0
    wrong_resolver: int = 0
    missed: int = 0
    extra: int = 0
    over_claimed: int = 0
    detail: list[dict[str, str]] = field(default_factory=list)


def _corpus() -> dict[str, dict[str, Any]]:
    return json.loads(CORPUS.read_text(encoding="utf-8"))["decisions"]


def _tool_name(key: str) -> str:
    """`mcp:github:create_issue` -> `create_issue`. The model sees what an agent sees."""
    return key.rsplit(":", 1)[-1]


def _specs_for_recovery(decisions: dict[str, dict[str, Any]]) -> list[Any]:
    """The gated tools, rebuilt with every parameter eligible and the answer key withheld."""
    from neti.insight.discover import DeclinedParam, ToolSpec

    out = []
    for key, entry in sorted(decisions.items()):
        if not entry["gated"]:
            continue
        params = sorted(
            {g["pointer"].lstrip("/").split("#")[0] for g in entry["gated"]}
            | {d["param"] for d in entry["declined"]}
        )
        out.append(
            ToolSpec(
                name=_tool_name(key),
                description=entry.get("description", ""),
                params=tuple(params),
                gated=(),
                destructive=entry.get("destructive", False),
                # Everything eligible: no `would_be` anywhere, so nothing is filtered out.
                declined=tuple(DeclinedParam(param=p, why="withheld") for p in params),
            )
        )
    return out


def _expected(decisions: dict[str, dict[str, Any]]) -> dict[tuple[str, str], str]:
    """(tool, parameter) -> resolver, from the committed key."""
    out: dict[tuple[str, str], str] = {}
    for key, entry in decisions.items():
        for gate in entry["gated"]:
            param = gate["pointer"].lstrip("/").split("#")[0]
            out[(_tool_name(key), param)] = gate["resolver"]
    return out


def run(client: Any, *, batch_size: int, limit: int | None) -> Arm:
    from neti.insight.assist import SYSTEM, batches, eligible, parse, payload, schema

    decisions = _corpus()
    specs = _specs_for_recovery(decisions)
    if limit:
        specs = specs[:limit]
    expected = _expected(decisions)

    arm = Arm(of=len({k for k in expected if k[0] in {s.name for s in specs}}))
    claimed: dict[tuple[str, str], str] = {}

    groups = batches(eligible(specs), size=batch_size)
    for index, group in enumerate(groups, start=1):
        print(f"  batch {index}/{len(groups)} ({len(group)} parameters)", file=sys.stderr)
        answer = client.ask(SYSTEM, json.dumps(payload(group), sort_keys=True), schema())
        got, bad = parse(answer.text, group)
        for suggestion in got:
            claimed[(suggestion.tool, suggestion.parameter)] = suggestion.resolver
        for rejection in bad:
            if rejection.reason != "unanswered":
                arm.detail.append({"rejected": rejection.reason, "what": rejection.detail})

    names = {s.name for s in specs}
    for key, resolver in expected.items():
        if key[0] not in names:
            continue
        got_resolver = claimed.get(key)
        if got_resolver is None:
            arm.missed += 1
            arm.detail.append({"missed": f"{key[0]}/{key[1]}", "expected": resolver})
        elif got_resolver == resolver:
            arm.recovered += 1
        else:
            arm.wrong_resolver += 1
            arm.detail.append(
                {"wrong": f"{key[0]}/{key[1]}", "expected": resolver, "got": got_resolver}
            )

    for key, resolver in claimed.items():
        if key not in expected:
            arm.extra += 1
            arm.detail.append({"extra": f"{key[0]}/{key[1]}", "got": resolver})

    return arm


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="anthropic", choices=["anthropic", "openai", "local"])
    parser.add_argument("--base-url", default=None, help="For --provider local.")
    parser.add_argument("--timeout", type=float, default=None, help="Seconds per request.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None, help="First N gated tools only.")
    args = parser.parse_args()

    from neti.insight.assist_client import client_for

    try:
        client = client_for(args.provider, args.model, base_url=args.base_url)
        if args.timeout and hasattr(client, "timeout_s"):
            client.timeout_s = args.timeout
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    import os

    # A local runner needs no key, which is most of the point of having one.
    key = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(args.provider, "")
    if key and not os.environ.get(key):
        print(
            f"error: {key} is not set.\n\n"
            "This is your key and your account: neti never proxies the request and this harness\n"
            "is the same code path `neti suggest` uses. Nothing is sent anywhere else.\n\n"
            f"  {key}=... just assist",
            file=sys.stderr,
        )
        return 2

    print("M12 arm A — recovery against the committed answer key", file=sys.stderr)
    arm = run(client, batch_size=args.batch_size, limit=args.limit)

    payload = {
        "metric": "M12",
        "arm": "recovery",
        "provider": args.provider,
        "model": client.name,
        "recovery": {k: v for k, v in asdict(arm).items() if k != "detail"},
        "detail": arm.detail,
    }
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # The wrong count first, which is the rule the incident table already follows.
    print(
        f"\nof {arm.of} gates the rule table makes on these tools, the model got "
        f"{arm.wrong_resolver} wrong, missed {arm.missed}, and claimed {arm.extra} the rule table "
        f"declined.\nIt recovered {arm.recovered}.\n\nwrote {RESULTS.relative_to(REPO)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
