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
    unassisted: int = 0
    """Parameters no answer was obtained for.

    Reported separately, because a miss and a question never asked are different things and only
    one of them says anything about the model.
    """
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


def _specs_for_over_claim(decisions: dict[str, dict[str, Any]]) -> list[Any]:
    """The 41 the rule table declined *with a written reason*, offered up deliberately.

    `neti suggest` structurally cannot send these — `eligible()` drops anything with a `would_be`,
    so the shipped command never asks a model to overturn a judgement somebody already made in
    writing. This arm sends exactly those, because every claim on them is an over-claim by
    construction and that makes the rate measurable rather than assumed.

    It is the instrument for the failure the whole feature is arranged around: "this search string
    is SQL". `Grep/pattern` is a search pattern and not a set of paths; `WebSearch/query` is a
    search string and not a `DELETE`. A model that confidently claims those is a model whose
    suggestions cost more to review than they save.
    """
    from neti.insight.discover import DeclinedParam, ToolSpec

    out = []
    for key, entry in sorted(decisions.items()):
        contested = [d for d in entry["declined"] if d["would_be"]]
        if not contested:
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
                # The contested ones only, and stripped of `would_be` so they are askable here.
                declined=tuple(DeclinedParam(param=d["param"], why="withheld") for d in contested),
            )
        )
    return out


def _contested(decisions: dict[str, dict[str, Any]]) -> dict[tuple[str, str], dict[str, str]]:
    """(tool, parameter) -> the resolver the rule table rejected, and why it rejected it."""
    out: dict[tuple[str, str], dict[str, str]] = {}
    for key, entry in decisions.items():
        for declined in entry["declined"]:
            if declined["would_be"]:
                out[(_tool_name(key), declined["param"])] = {
                    "would_be": declined["would_be"],
                    "why": declined["why"],
                }
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
        # One bad batch must not lose the run. A local runner closing a connection twenty minutes
        # in used to raise straight out of here and discard every batch that had already worked,
        # which is a bad way to spend half an hour. The CLI already survived this; the harness did
        # not, and an eval that cannot finish produces no number at all.
        try:
            answer = client.ask(SYSTEM, json.dumps(payload(group), sort_keys=True), schema())
        except Exception as exc:
            arm.unassisted += len(group)
            arm.detail.append({"batch_failed": f"{index}/{len(groups)}", "why": str(exc)[:160]})
            print(f"    failed, continuing: {str(exc)[:90]}", file=sys.stderr)
            continue
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


def run_over_claim(client: Any, *, batch_size: int) -> Arm:
    """Arm B. Every claim here is wrong by construction, so the number is the over-claim rate."""
    from neti.insight.assist import SYSTEM, batches, eligible, parse, payload, schema

    decisions = _corpus()
    contested = _contested(decisions)
    specs = _specs_for_over_claim(decisions)
    arm = Arm(of=len(contested))

    groups = batches(eligible(specs), size=batch_size)
    for index, group in enumerate(groups, start=1):
        print(f"  batch {index}/{len(groups)} ({len(group)} parameters)", file=sys.stderr)
        try:
            answer = client.ask(SYSTEM, json.dumps(payload(group), sort_keys=True), schema())
        except Exception as exc:
            arm.unassisted += len(group)
            arm.detail.append({"batch_failed": f"{index}/{len(groups)}", "why": str(exc)[:160]})
            continue
        got, _ = parse(answer.text, group)
        for suggestion in got:
            key = (suggestion.tool, suggestion.parameter)
            known = contested.get(key)
            if known is None:
                continue
            arm.over_claimed += 1
            arm.detail.append(
                {
                    "over_claimed": f"{key[0]}/{key[1]}",
                    "model_said": suggestion.resolver,
                    "rule_table_rejected": known["would_be"],
                    "because": known["why"][:150],
                }
            )
    return arm


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="anthropic", choices=["anthropic", "openai", "local"])
    parser.add_argument("--base-url", default=None, help="For --provider local.")
    parser.add_argument("--timeout", type=float, default=None, help="Seconds per request.")
    parser.add_argument("--model", default=None)
    # 16, and the number is measured rather than picked. A local runner re-reads the whole system
    # prompt every request, so wall-clock is dominated by the request count — but accuracy falls off
    # once a batch gets large enough that the model starts skimming. Against the committed key on
    # llama3:
    #
    #     batch  8  (12 requests)   recovered 30/34
    #     batch 16  ( 5 requests)   recovered 30/34
    #     batch 40  ( 3 requests)   recovered 27/34
    #
    # 16 is where the curve flattens: the same answer as 8 for less than half the requests, and the
    # three parameters 40 loses are ones it plainly knows (`Glob/pattern`, `query/sql`).
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--limit", type=int, default=None, help="First N gated tools only.")
    parser.add_argument(
        "--arm",
        default="both",
        choices=["recovery", "over-claim", "both"],
        help="recovery is the go/no-go; over-claim is the risk.",
    )
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

    recovery = over_claim = None

    if args.arm in {"recovery", "both"}:
        print("M12 arm A — recovery against the committed answer key", file=sys.stderr)
        recovery = run(client, batch_size=args.batch_size, limit=args.limit)

    if args.arm in {"over-claim", "both"}:
        print("M12 arm B — over-claim on the parameters already declined", file=sys.stderr)
        over_claim = run_over_claim(client, batch_size=args.batch_size)

    result: dict[str, Any] = {
        "metric": "M12",
        "provider": args.provider,
        "model": client.name,
    }
    detail: list[dict[str, str]] = []
    if recovery is not None:
        result["recovery"] = {k: v for k, v in asdict(recovery).items() if k != "detail"}
        detail.extend(recovery.detail)
    if over_claim is not None:
        result["over_claim"] = {k: v for k, v in asdict(over_claim).items() if k != "detail"}
        detail.extend(over_claim.detail)
    result["detail"] = detail

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # The wrong counts first, which is the rule the incident table already follows.
    print()
    if recovery is not None:
        print(
            f"recovery   of {recovery.of} gates the rule table already makes, the model got "
            f"{recovery.wrong_resolver} wrong, missed {recovery.missed},\n"
            f"           and claimed {recovery.extra} it had declined. It recovered "
            f"{recovery.recovered}."
        )
    if over_claim is not None:
        print(
            f"over-claim of {over_claim.of} parameters the rule table declined *with a written "
            f"reason*, the model\n"
            f"           claimed {over_claim.over_claimed}. Every one of those is an over-claim by "
            "construction.\n"
            "           `neti suggest` never sends these; this arm exists to measure the appetite."
        )
    print(f"\nwrote {RESULTS.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
