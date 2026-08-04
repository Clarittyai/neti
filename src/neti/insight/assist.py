"""Ask *your* model which unclaimed parameters name a set. Nothing it says reaches a decision.

`neti init` gates what its rule table can claim. Against the 170 real tool schemas in
`tests/corpus/` that is 31 tools; it declines 41 parameters with a written reason and leaves **401
with no rule at all**. Most of those 401 genuinely are not sets — a page size, a cursor, one issue
number. Some are, and the only thing standing between them and a gate is somebody reading them.

This module prepares that reading for a model, and handles the answer. It is the same posture as
`neti propose`, which turns observed traffic into ceilings a human commits: **a config-authoring
aid, read by a person, that never touches the decision path.** Four things keep that true rather
than merely stated:

1. **The model is never asked for a quantity.** Not a magnitude, not a direction, not a unit, not a
   ceiling, not a verdict. It is asked which of seven shipped resolvers could size a parameter, from
   a closed enum, or to say the parameter is not a set. Direction is declared by the resolver and
   units belong to the parameter's role; both come from the rule table and nowhere else.
2. **The output is inert.** `render_fragment` emits YAML in which every block is commented out and
   every `bands:` is empty. Deleting the `#` is the human's confirmation, and even then an empty
   band list resolves and records but cannot block anything.
3. **The contested 41 are never sent.** The rule table already considered those exact resolvers and
   rejected them *with a reason*. Letting a model overturn a written judgement is precisely the "a
   search string is SQL" failure, and there is no upside to asking.
4. **It is scored.** `eval/harness/assist.py` measures this against `tests/corpus/decisions.json`
   and reports the wrong count before the right one. A trial that does not end as a number on
   `neti score` does not count.

Nothing here opens a socket. `assist_client.py` is the only module that does, and it talks to the
operator's own provider with the operator's own key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from neti.insight.discover import ToolSpec, proposable_resolvers

MAX_DESCRIPTION = 200
"""How much of a description travels. The first line and no more, matching what `classify` keeps."""

NOT_A_SET = "not_a_set"
NO_SHIPPED_RESOLVER = "no_shipped_resolver"

# The escape valve, and it is load-bearing. A model told "pick one of seven or say not_a_set" will
# force a Slack channel into `entra.principals`, because a channel *is* a set of people and the
# answer sheet has nowhere else to put it. With this, the near-miss becomes a line in a list of
# resolvers somebody could write, which is `RESOLVER_CONTRACT.md`'s whole invitation.
ANSWERS = (NOT_A_SET, NO_SHIPPED_RESOLVER)


@dataclass(frozen=True)
class Candidate:
    """One parameter worth asking about, and the company it keeps."""

    tool: str
    parameter: str
    description: str
    siblings: tuple[str, ...]


@dataclass(frozen=True)
class Suggestion:
    """A model's claim that a parameter names a set one shipped resolver could size."""

    tool: str
    parameter: str
    resolver: str
    why: str


@dataclass(frozen=True)
class Rejection:
    """An answer that was thrown away, and which rule threw it.

    Counted and reported rather than silently dropped: "3 discarded (2 named a parameter we did not
    send, 1 duplicate)" is a sentence an operator can act on, and a silent drop is how a bad
    response becomes an invisible one.
    """

    reason: str
    detail: str


def eligible(tools: list[ToolSpec]) -> tuple[Candidate, ...]:
    """The parameters worth asking about: declined, with no near-miss already written down.

    `DeclinedParam.would_be` is set when a resolver's name-rule matched and its context test failed
    — `query` on a search server, `owner` next to `repo`. Those are the contested 41, and they are
    excluded here rather than filtered later, so the shipped command structurally cannot ask a model
    to overturn a judgement the rule table already made in writing.
    """
    out: list[Candidate] = []
    for tool in tools:
        siblings = tuple(sorted(tool.params))
        for declined in tool.declined:
            if declined.would_be is not None:
                continue
            out.append(
                Candidate(
                    tool=tool.name,
                    parameter=declined.param,
                    description=_first_line(tool.description),
                    siblings=siblings,
                )
            )
    return tuple(out)


def _first_line(text: str) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line[:MAX_DESCRIPTION]


def batches(candidates: tuple[Candidate, ...], *, size: int) -> tuple[tuple[Candidate, ...], ...]:
    """Group by tool, then chunk. A tool's parameters are judged together or not at all.

    Splitting one tool across two requests would show the model half its siblings, and siblings are
    the discriminator the rule table itself leans on: `path` next to `owner` and `repo` is a path
    inside a repository, not a path on this machine.
    """
    by_tool: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_tool.setdefault(candidate.tool, []).append(candidate)

    out: list[tuple[Candidate, ...]] = []
    current: list[Candidate] = []
    for group in by_tool.values():
        if current and len(current) + len(group) > size:
            out.append(tuple(current))
            current = []
        current.extend(group)
    if current:
        out.append(tuple(current))
    return tuple(out)


def payload(batch: tuple[Candidate, ...], *, scrub: bool = True) -> list[dict[str, Any]]:
    """Exactly what leaves the machine, and nothing else.

    Tool names, parameter names, sibling *names*, and the first line of each description. Never the
    policy, never the ceilings, never the records, never a server's command line or environment,
    never a path. `neti suggest --dry-run` prints this and exits before any network call, and
    `tests/property/test_assist_payload.py` asserts the key set is exactly this.
    """
    by_tool: dict[str, dict[str, Any]] = {}
    for candidate in batch:
        entry = by_tool.setdefault(
            candidate.tool,
            {
                "tool": candidate.tool,
                "description": _clean(candidate.description) if scrub else candidate.description,
                "siblings": list(candidate.siblings),
                "parameters": [],
            },
        )
        entry["parameters"].append(candidate.parameter)
    return list(by_tool.values())


def _clean(text: str) -> str:
    """Drop any token that looks like a credential before it leaves the machine.

    A tool description containing an example `ghp_…` is not hypothetical, and the operator did not
    choose to send it anywhere. Reuses the value rules that keep credentials out of the record.
    """
    from neti.core.redact import looks_secret

    return " ".join("<redacted>" if looks_secret(word) else word for word in text.split())


def schema() -> dict[str, Any]:
    """The response shape, with the resolver set closed.

    Derived from `proposable_resolvers()` so a rule added or a resolver retired cannot leave this
    enum stale — and so a model cannot name `github.files`, which `neti init` deliberately never
    proposes and `render_fragment` could not express even if the claim were right.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["claims"],
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["tool", "parameter", "resolver", "why"],
                    "properties": {
                        "tool": {"type": "string"},
                        "parameter": {"type": "string"},
                        "resolver": {"enum": [*proposable_resolvers(), *ANSWERS]},
                        "why": {"type": "string"},
                    },
                },
            }
        },
    }


SYSTEM = """\
You are helping an operator configure `neti`, a preflight gate that resolves how many things a
proposed tool call will touch and compares that count to a ceiling the operator declared.

Your only job: for each parameter you are shown, say whether it names a SET whose size one of the
resolvers below could measure, and which one. You are not asked for a number, a ceiling, a
direction, or a risk judgement, and you will not be given any.

A wrong claim is worse than no claim. Every claim the operator accepts binds a resolver to a
parameter; if the parameter is not what you thought, every call records "could not resolve" and the
operator finds that out only afterwards. When the evidence in front of you does not settle it,
answer not_a_set.

The resolvers that ship. These are the only values you may use.

  entra.principals    counts the transitive members of a directory group, by object id or group
                      name. Claim it only when the parameter names a group, team or distribution
                      list in an identity directory. Not a mailing list on a SaaS product, not a
                      chat channel, not a role string.
  entra.apps          counts the applications a directory group grants access to. Same target as
                      entra.principals; claim it only alongside one.
  fs.paths            walks a path or glob on THIS machine and counts files, up to a cap. Claim it
                      for a local filesystem path or glob. Not a path inside a repository, not an
                      object-store key, not a URL path, and not a pattern that matches file
                      *contents* rather than naming files.
  db.rows             parses a SQL statement and counts the rows its predicate matches with
                      `select count(*)`. It recognises `DELETE FROM t [WHERE p]` and
                      `UPDATE t SET ... [WHERE p]` and nothing else. Claim it only when the
                      parameter certainly carries SQL. A web search string, a query DSL, a GraphQL
                      document, a natural-language question, and a SELECT are all not_a_set.
  storage.objects     lists an object-store bucket or prefix (s3://bucket/prefix) and counts
                      objects, up to a cap. Claim it only when the parameter addresses an object
                      store.
  github.repos        counts the repositories under a GitHub owner or organisation. Claim it only
                      when the owner IS the target. If a sibling parameter names a single
                      repository, the call touches that repository and the owner is an address, not
                      a target: not_a_set.
  terraform.destroy   reads a Terraform plan document and counts the resources it destroys or
                      replaces. Claim it only for a plan, a plan file, or plan JSON.

  not_a_set           the parameter is a single value, a flag, a page size, a cursor, an identifier
                      for one object, free text, or anything else that does not name a set. This is
                      the correct answer for most parameters.
  no_shipped_resolver the parameter genuinely names a set, but none of the resolvers above could
                      measure it — a chat channel's members, the pages in a database. Use this
                      rather than forcing a near-miss.

Rules.
1. Answer for every parameter you are shown, exactly once. Do not answer for any other.
2. Judge each parameter in the company it keeps. Sibling parameter names are given for exactly this
   reason: `path` next to `owner` and `repo` is a path inside a repository.
3. Never invent a resolver name. Never give two resolvers for one parameter.
4. `why` is one sentence addressed to the operator, naming the evidence you used. Say what you read,
   not what you assume.
"""


def parse(raw: str, batch: tuple[Candidate, ...]) -> tuple[list[Suggestion], list[Rejection]]:
    """Read a response strictly. Anything that does not fit is a counted rejection, never a guess.

    No repair retry and no partial merge. A half-read answer looks exactly like a short one, which
    is `RESOLVER_CONTRACT.md`'s rule about `PARTIAL` being unmergeable, applied to a model.
    """
    asked = {(c.tool, c.parameter) for c in batch}
    allowed = set(proposable_resolvers())

    try:
        data = json.loads(raw)
        claims = data["claims"]
        if not isinstance(claims, list):
            raise TypeError("claims is not a list")
    except Exception as exc:
        return [], [Rejection("malformed", f"{type(exc).__name__}: {exc}")]

    suggestions: list[Suggestion] = []
    rejections: list[Rejection] = []
    seen: set[tuple[str, str]] = set()
    duplicated: set[tuple[str, str]] = set()

    for claim in claims:
        if not isinstance(claim, dict):
            rejections.append(Rejection("malformed", repr(claim)[:120]))
            continue
        key = (str(claim.get("tool", "")), str(claim.get("parameter", "")))
        if key not in asked:
            rejections.append(Rejection("unknown_param", f"{key[0]}/{key[1]}"))
            continue
        if key in seen:
            duplicated.add(key)
            continue
        seen.add(key)

        resolver = str(claim.get("resolver", ""))
        if resolver in ANSWERS:
            continue
        if resolver not in allowed:
            rejections.append(Rejection("unknown_resolver", f"{key[0]}/{key[1]}: {resolver}"))
            continue
        why = str(claim.get("why", "")).strip()
        if not why:
            rejections.append(Rejection("no_reason", f"{key[0]}/{key[1]}"))
            continue
        suggestions.append(Suggestion(key[0], key[1], resolver, why))

    # A set of two answers is no answer, so both copies go and the pair is reported.
    for key in duplicated:
        suggestions = [s for s in suggestions if (s.tool, s.parameter) != key]
        rejections.append(Rejection("duplicate", f"{key[0]}/{key[1]}"))

    unanswered = sorted(asked - seen)
    rejections.extend(Rejection("unanswered", f"{t}/{p}") for t, p in unanswered)
    return suggestions, rejections


HEADER = """\
# ══════════════════════════════════════════════════════════════════════════════
#  SUGGESTED BY A MODEL. UNVERIFIED. NOTHING IN THIS FILE IS ACTIVE.
#
#  {model} via {provider}, called with YOUR key from YOUR machine.
#
#  `neti init`'s rule table could not claim these parameters, so a model was asked which of the
#  shipped resolvers could size them. It was not asked for a number, a direction or a ceiling, and
#  it never saw your policy, your traffic or your credentials.
#
#  A model is not evidence. Uncomment a block only when you have read the parameter yourself and
#  agree with the sentence above it. Then run a week in observe mode: a suggestion that was wrong
#  shows up as UNRESOLVED on every call, in `neti report`, before any ceiling exists to block on.
#
#  Merge what survives into neti.yaml by hand. `neti suggest` never edits it.
# ══════════════════════════════════════════════════════════════════════════════
"""


def render_fragment(
    suggestions: list[Suggestion], *, model: str, provider: str, unsized: list[Suggestion]
) -> str:
    """A YAML fragment in which nothing is on.

    Every block is commented out and every `bands:` is empty, so uncommenting is the human's
    confirmation and even then the gate resolves and records without being able to block. Three
    things make a suggestion visibly different from a rule-table gate: it is in a different file,
    which `neti gate` never loads; it is commented out; and its reason is prefixed UNVERIFIED and
    carries the failure mode if the claim is wrong.
    """
    lines = [HEADER.format(model=model, provider=provider)]

    if not suggestions:
        lines.append("# Nothing to suggest: the model claimed no parameter it was shown.\n")
    else:
        lines.append("# tools:")
        by_tool: dict[str, list[Suggestion]] = {}
        for s in suggestions:
            by_tool.setdefault(s.tool, []).append(s)
        for tool, group in sorted(by_tool.items()):
            lines.append(f"#   {tool}:")
            lines.append("#     gate:")
            for s in sorted(group, key=lambda x: x.parameter):
                lines.append(f"#       /{s.parameter}:")
                lines.append(f"#         # UNVERIFIED (model-suggested): {s.why}")
                lines.append(
                    f"#         # If that is wrong, delete this block. {_risk(s.resolver)}"
                )
                lines.append(f"#         resolver: {s.resolver}")
                lines.append("#         bands: [] # <- your numbers go here, from your own traffic")
                lines.append(
                    "#         on_unresolved: confirm # a failed lookup is never read as 0"
                )

    if unsized:
        lines.append("")
        lines.append(
            "# Parameters that name a set nothing shipped can size. Not a policy: a list of"
        )
        lines.append("# resolvers somebody could write. See RESOLVER_CONTRACT.md.")
        for s in sorted(unsized, key=lambda x: (x.tool, x.parameter)):
            lines.append(f"#   {s.tool} /{s.parameter} — {s.why}")

    return "\n".join(lines) + "\n"


def _risk(resolver: str) -> str:
    """What it costs if this particular claim is wrong, per resolver."""
    return {
        "db.rows": "db.rows reports a LOWER_BOUND — cascades stay invisible (SCOPE NC-10) — so it "
        "can block but never allow. It needs NETI_DATABASE_URL on a read-only user.",
        "fs.paths": "fs.paths walks this machine. If the value is a path inside a repository or an "
        "object store, every call records UNRESOLVED.",
        "storage.objects": "storage.objects paginates and caps, reporting a LOWER_BOUND.",
        "github.repos": "github.repos counts an owner's repositories. If this names one repository "
        "rather than the owner, the number is about the wrong thing.",
        "entra.principals": "entra.principals counts a directory group. A chat channel or a role "
        "string resolves to nothing.",
        "entra.apps": "entra.apps counts applications assigned to a directory group.",
        "terraform.destroy": "terraform.destroy reads a plan document.",
    }.get(resolver, "")
