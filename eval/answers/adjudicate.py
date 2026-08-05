"""One careful reading of the 401 parameters no rule claims, written down so it can be argued with.

Arms A and B of M12 need no labelling: A scores against gates the rule table already makes, and
every claim in B is wrong by construction. Arm C is the question those two exist to earn — *of the
401 parameters no rule touches, which ones actually name a set?* — and it cannot be scored without
somebody deciding what the right answer is.

**This file is that somebody's opinion, and the number it produces is only as good as the opinion.**
It is a script rather than a hand-typed JSON file for one reason: a rule with a written rationale,
applied to every parameter it covers, is reviewable in a way that four hundred individual
judgement calls are not. Disagree with a rule here and you can see every entry it moved. The
generated `claimable.json` is committed alongside it so the diff of a re-run is the review.

Four labels:

    <resolver>            a shipped resolver could size this, and the rule table missed it
    no_shipped_resolver   it genuinely names a set, and nothing that ships could count it
    not_a_set             a flag, a page size, a cursor, an id for one record, free text
    unadjudicated         the schema in front of me does not settle it

`unadjudicated` is load-bearing and is never scored. The temptation in an exercise like this is to
label everything, because a complete key looks more finished — and every forced label becomes a
model being marked wrong for an answer nobody actually checked.

    python -m eval.answers.adjudicate            # rewrite claimable.json
    python -m eval.answers.adjudicate --check    # fail if it is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "tests" / "corpus" / "decisions.json"
TOOLS = REPO / "tests" / "corpus" / "tools.json"
TARGET = Path(__file__).resolve().parent / "claimable.json"

NOT_A_SET = "not_a_set"
NO_RESOLVER = "no_shipped_resolver"
UNADJUDICATED = "unadjudicated"

# --------------------------------------------------------------------------- the rules
#
# Each is a claim about a class of parameter, with the reason it is being made. They are applied in
# order and the first match wins, so the specific ones come before the general ones.

A_LOCAL_FILE_TO_WRITE = {
    # The rule table's fs.paths name-rule knows `path`, `file_path`, `filePath`, `notebook_path` and
    # `pattern`. It does not know `filename`, and every one of these is a path on this machine that
    # the call writes to or reads from. This is the clearest gap in the whole corpus: the resolver
    # exists, it ships, it would answer, and nothing points it here.
    ("browser_console_messages", "filename"),
    ("browser_evaluate", "filename"),
    ("browser_network_request", "filename"),
    ("browser_network_requests", "filename"),
    ("browser_run_code_unsafe", "filename"),
    ("browser_snapshot", "filename"),
    ("browser_take_screenshot", "filename"),
}

AN_ARRAY_OF_THINGS_THE_CALL_ACTS_ON = {
    # The magnitude is the argument's own length: no I/O, no credentials, no provider, nothing to
    # resolve. `delete_entities(entityNames=[…])` with two hundred names is exactly the question
    # this product asks, and neti ships nothing that counts a literal array. That is a resolver
    # somebody could write in an afternoon, which is what `no_shipped_resolver` is for.
    ("create_entities", "entities"),
    ("delete_entities", "entityNames"),
    ("open_nodes", "names"),
    ("add_observations", "observations"),
    ("delete_observations", "deletions"),
    ("create_relations", "relations"),
    ("edit_file", "edits"),
    ("push_files", "files"),
    ("API-patch-block-children", "children"),
    ("create_pull_request_review", "comments"),
    ("create_issue", "assignees"),
    ("create_issue", "labels"),
    ("update_issue", "assignees"),
    ("update_issue", "labels"),
    ("list_issues", "labels"),
    ("fill_form", "elements"),
    ("browser_fill_form", "fields"),
    ("browser_select_option", "values"),
}

AN_AUDIENCE_OR_A_HISTORY = {
    # A Slack channel names a set twice over: the people who will see the message, and the messages
    # already in it. Neither is countable by anything that ships, and both are the blast radius.
    # Note what is *not* here: `channel_id` on `slack_add_reaction` addresses one message, and the
    # channel is an address rather than a target — the same distinction the rule table already draws
    # for `owner` next to `repo`.
    ("slack_post_message", "channel_id"),
    ("slack_reply_to_thread", "channel_id"),
    ("slack_get_channel_history", "channel_id"),
    ("slack_get_thread_replies", "thread_ts"),
}

A_QUERY_THAT_RETURNS_A_SET = {
    # GitHub's search API answers these with a `total_count`, so the magnitude is not hypothetical.
    # Deliberately *not* labelled `db.rows`: that is the exact "a search string is SQL" error Arm B
    # measures, and the honest answer is that a resolver for this does not exist yet.
    ("search_code", "q"),
    ("search_issues", "q"),
    ("search_users", "q"),
    # A regular expression over a page snapshot matches an unbounded number of nodes.
    ("browser_find", "regex"),
    ("browser_find", "text"),
}

A_CONTAINER_THAT_FANS_OUT = {
    # The id names one object, but the call reaches everything under it. `block_id` on
    # `API-get-block-children` is not a single block, it is however many children that block has.
    ("API-get-block-children", "block_id"),
    ("API-patch-block-children", "block_id"),
    ("API-query-data-source", "data_source_id"),
}

I_CANNOT_TELL = {
    # Left unlabelled on purpose, and never scored. Each of these could go either way on evidence
    # not present in the schema, and a guess here would be indistinguishable from a judgement.
    ("API-retrieve-a-database", "database_id"),
    ("API-list-data-source-templates", "data_source_id"),
    ("API-retrieve-a-page-property", "property_id"),
    ("API-post-search", "filter"),
    ("API-query-data-source", "filter"),
    ("API-create-a-data-source", "properties"),
    ("API-update-a-data-source", "properties"),
    # A shell command and a subagent prompt can both touch anything at all. Nothing can parse them
    # into a count, and calling that `not_a_set` would understate them badly — the seam that bounds
    # these is the sandbox, not a number.
    ("Bash", "command"),
    ("Task", "prompt"),
    ("puppeteer_evaluate", "script"),
    ("browser_run_code_unsafe", "code"),
    ("evaluate_script", "function"),
    ("browser_evaluate", "function"),
}

WHY = {
    "fs.paths": "a path on this machine that the call writes to or reads from; fs.paths ships and "
    "would answer, but its name-rule does not know `filename`",
    "array": "a literal array of the things the call acts on: the magnitude is the argument's own "
    "length, and nothing that ships counts it",
    "audience": "a channel names both an audience and a history, and neither is countable by "
    "anything that ships",
    "query": "the call answers with a result set whose size is real and reportable; deliberately "
    "not db.rows, which is the error arm B measures",
    "container": "the id names one object but the call reaches everything under it",
    "unsure": "the schema in front of me does not settle this, and a guess would look like a "
    "judgement",
    "default": "an address, a flag, a page size, a cursor, an id for one record, or free text: "
    "nothing here addresses a set",
}


def adjudicate(tool: str, param: str) -> tuple[str, str]:
    """One (tool, parameter) pair, and the reason for the label it gets."""
    pair = (tool, param)
    if pair in I_CANNOT_TELL:
        return UNADJUDICATED, WHY["unsure"]
    if pair in A_LOCAL_FILE_TO_WRITE:
        return "fs.paths", WHY["fs.paths"]
    if pair in AN_ARRAY_OF_THINGS_THE_CALL_ACTS_ON:
        return NO_RESOLVER, WHY["array"]
    if pair in AN_AUDIENCE_OR_A_HISTORY:
        return NO_RESOLVER, WHY["audience"]
    if pair in A_QUERY_THAT_RETURNS_A_SET:
        return NO_RESOLVER, WHY["query"]
    if pair in A_CONTAINER_THAT_FANS_OUT:
        return NO_RESOLVER, WHY["container"]
    return NOT_A_SET, WHY["default"]


def unclaimed() -> list[tuple[str, str]]:
    """Every parameter the rule table left with no rule at all — the 401."""
    decisions = json.loads(CORPUS.read_text(encoding="utf-8"))["decisions"]
    tools = {t["id"]: t for t in json.loads(TOOLS.read_text(encoding="utf-8"))["tools"]}
    out = []
    for key, entry in sorted(decisions.items()):
        name = tools[key]["name"] if key in tools else key.split(":")[-1]
        for declined in entry["declined"]:
            if declined.get("would_be") is None:
                out.append((name, declined["param"]))
    return out


def expected() -> dict[str, Any]:
    labels: dict[str, dict[str, Any]] = {}
    for tool, param in unclaimed():
        label, why = adjudicate(tool, param)
        labels.setdefault(tool, {})[param] = {"label": label, "why": why}

    counts: dict[str, int] = {}
    for params in labels.values():
        for entry in params.values():
            counts[entry["label"]] = counts.get(entry["label"], 0) + 1

    return {
        "version": 1,
        "note": (
            "Arm C's answer key: what the 401 parameters no rule claims actually address. "
            "Generated by eval/answers/adjudicate.py, where every label carries the rule that "
            "produced it. This is one reading, not a fact — argue with the rules, not the JSON. "
            "`unadjudicated` entries are excluded from every rate M12 reports."
        ),
        "counts": dict(sorted(counts.items())),
        "labels": labels,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="do not write; exit 1 if stale")
    args = parser.parse_args()

    want = expected()
    text = json.dumps(want, indent=2, sort_keys=True) + "\n"
    have = TARGET.read_text(encoding="utf-8") if TARGET.exists() else None

    if have == text:
        if args.check:
            print(f"{TARGET.name} is current")
        return 0
    if args.check:
        print(
            f"{TARGET.name} is {'missing' if have is None else 'stale'}. "
            "Run `python -m eval.answers.adjudicate`.",
            file=sys.stderr,
        )
        return 1

    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {TARGET.name}")
    for label, count in sorted(want["counts"].items()):
        print(f"  {count:4d}  {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
