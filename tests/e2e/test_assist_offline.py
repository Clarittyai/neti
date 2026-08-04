"""`neti suggest`'s machinery, with the model replaced by a recorded answer.

The value of this file is that it pins everything except the one thing that cannot be pinned. What
is sent, what is accepted, what is thrown away and what is rendered are all deterministic and
asserted here; only the model's judgement is left to `eval/harness/assist.py`, which scores it
against `tests/corpus/decisions.json` and needs somebody's key.

The posture being tested is the whole point of the feature. A model's answer must be able to be
wrong without anything bad happening, and that is a property of this code rather than of the model:
the contested parameters are never asked about, the enum is closed, an unrecognised answer is
counted rather than guessed at, and the output is inert YAML a human has to edit before it does
anything at all.
"""

from __future__ import annotations

import json

import pytest

from neti.insight.assist import (
    ANSWERS,
    Candidate,
    Suggestion,
    batches,
    eligible,
    parse,
    payload,
    render_fragment,
    schema,
)
from neti.insight.discover import DeclinedParam, ToolSpec, proposable_resolvers

TOOL = ToolSpec(
    name="supabase__execute_sql",
    description="Run a raw SQL query against the database.\nSecond line, dropped.",
    params=("query", "read_only", "project_id"),
    gated=(),
    destructive=True,
    declined=(
        DeclinedParam(param="read_only", why="a flag"),
        DeclinedParam(param="project_id", why="one project"),
        # The contested shape: a rule matched by name and its context test failed. Never sent.
        DeclinedParam(
            param="query", why="this server searches, it does not execute", would_be="db.rows"
        ),
    ),
)


def _batch() -> tuple[Candidate, ...]:
    return eligible([TOOL])


# ---------------------------------------------------------------------------- what is asked


def test_the_contested_parameters_are_never_sent() -> None:
    """The rule table already declined `query` here, in writing, with a reason.

    Asking a model to overturn that is exactly the "a search string is SQL" failure the reason was
    written to prevent, and there is no upside. Excluded when the batch is built rather than
    filtered afterwards, so the shipped command structurally cannot ask.
    """
    sent = {c.parameter for c in _batch()}
    assert sent == {"read_only", "project_id"}
    assert "query" not in sent


def test_the_payload_carries_nothing_but_names_and_a_first_line() -> None:
    """What leaves the machine, asserted as an exact key set rather than by inspection."""
    body = payload(_batch())
    assert [sorted(entry) for entry in body] == [["description", "parameters", "siblings", "tool"]]
    entry = body[0]
    assert entry["tool"] == "supabase__execute_sql"
    assert entry["description"] == "Run a raw SQL query against the database."
    assert "Second line" not in entry["description"], "only the first line travels"
    # Siblings are names only, and they are the discriminator the rule table itself leans on.
    assert entry["siblings"] == ["project_id", "query", "read_only"]


def test_a_credential_in_a_description_is_scrubbed_before_it_leaves() -> None:
    """Not hypothetical, and the operator did not choose to send it anywhere."""
    leaky = ToolSpec(
        name="deploy",
        description="Deploy with token " + "ghp" + "_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
        params=("target",),
        gated=(),
        destructive=False,
        declined=(DeclinedParam(param="target", why="unclear"),),
    )
    body = payload(eligible([leaky]))
    assert "<redacted>" in body[0]["description"]
    assert "ghp" + "_ABCDEF" not in body[0]["description"]


def test_a_tools_parameters_are_never_split_across_batches() -> None:
    """Half a tool's siblings is a different question from all of them."""
    many = [
        ToolSpec(
            name=f"t{i}",
            description="",
            params=("a", "b", "c"),
            gated=(),
            destructive=False,
            declined=tuple(DeclinedParam(param=p, why="?") for p in ("a", "b", "c")),
        )
        for i in range(4)
    ]
    for batch in batches(eligible(many), size=4):
        tools = {c.tool for c in batch}
        for tool in tools:
            in_batch = {c.parameter for c in batch if c.tool == tool}
            assert in_batch == {"a", "b", "c"}, f"{tool} was split across batches"


def test_the_enum_is_the_derived_resolver_set_and_nothing_else() -> None:
    """A model cannot name a resolver `neti init` could not render into a policy."""
    enum = schema()["properties"]["claims"]["items"]["properties"]["resolver"]["enum"]
    assert set(enum) == set(proposable_resolvers()) | set(ANSWERS)
    assert "github.files" not in enum, "NEVER_PROPOSED must not be reachable"
    assert json.dumps(schema()), "the schema has to be JSON-serialisable to be sent"


# ---------------------------------------------------------------------------- what is accepted


def _reply(claims: list[dict[str, str]]) -> str:
    return json.dumps({"claims": claims})


def test_a_good_answer_becomes_a_suggestion() -> None:
    batch = _batch()
    got, rejected = parse(
        _reply(
            [
                {
                    "tool": "supabase__execute_sql",
                    "parameter": "read_only",
                    "resolver": "not_a_set",
                    "why": "a boolean flag",
                },
                {
                    "tool": "supabase__execute_sql",
                    "parameter": "project_id",
                    "resolver": "fs.paths",
                    "why": "the description calls it a path",
                },
            ]
        ),
        batch,
    )
    assert [s.parameter for s in got] == ["project_id"], "not_a_set is an answer, not a suggestion"
    assert not rejected


@pytest.mark.parametrize(
    "claim, reason",
    [
        (
            {
                "tool": "supabase__execute_sql",
                "parameter": "read_only",
                "resolver": "made.up",
                "why": "x",
            },
            "unknown_resolver",
        ),
        (
            {
                "tool": "supabase__execute_sql",
                "parameter": "nope",
                "resolver": "fs.paths",
                "why": "x",
            },
            "unknown_param",
        ),
        (
            {"tool": "other", "parameter": "read_only", "resolver": "fs.paths", "why": "x"},
            "unknown_param",
        ),
        (
            {
                "tool": "supabase__execute_sql",
                "parameter": "read_only",
                "resolver": "fs.paths",
                "why": "  ",
            },
            "no_reason",
        ),
    ],
)
def test_a_bad_answer_is_counted_not_guessed_at(claim: dict[str, str], reason: str) -> None:
    """Every rejection is typed, so "3 discarded" can say which three and why."""
    got, rejected = parse(_reply([claim]), _batch())
    assert not got
    assert reason in {r.reason for r in rejected}


def test_the_same_parameter_answered_twice_is_dropped_entirely() -> None:
    """A set of two answers is no answer. Both copies go."""
    twice = {"tool": "supabase__execute_sql", "parameter": "project_id", "why": "x"}
    got, rejected = parse(
        _reply([{**twice, "resolver": "fs.paths"}, {**twice, "resolver": "db.rows"}]), _batch()
    )
    assert not got, "neither answer may survive"
    assert "duplicate" in {r.reason for r in rejected}


def test_a_parameter_left_unanswered_is_reported() -> None:
    """Silent omission would make coverage unmeasurable, so nothing falls through unremarked."""
    _, rejected = parse(_reply([]), _batch())
    assert {r.reason for r in rejected} == {"unanswered"}
    assert len(rejected) == 2


def test_a_truncated_response_yields_nothing() -> None:
    """A half-read answer looks exactly like a short one. No repair, no partial merge."""
    got, rejected = parse('{"claims": [{"tool": "supa', _batch())
    assert not got
    assert [r.reason for r in rejected] == ["malformed"]


# ---------------------------------------------------------------------------- what is written


def _fragment() -> str:
    return render_fragment(
        [Suggestion("supabase__execute_sql", "project_id", "db.rows", "the tool executes SQL")],
        model="a-model",
        provider="a-provider",
        unsized=[Suggestion("slack__history", "channel_id", "no_shipped_resolver", "a channel")],
    )


def test_every_suggested_line_is_commented_out() -> None:
    """Uncommenting is the human's confirmation, so nothing may arrive already active."""
    for line in _fragment().splitlines():
        assert not line.strip() or line.lstrip().startswith("#"), f"live YAML: {line!r}"


def test_the_fragment_is_not_loadable_as_a_policy() -> None:
    """The strongest form of "inert": there is no document here at all."""
    import yaml

    assert yaml.safe_load(_fragment()) is None


def test_it_says_it_is_unverified_and_what_happens_if_it_is_wrong() -> None:
    text = _fragment()
    assert "UNVERIFIED" in text
    assert "delete this block" in text
    assert "bands: []" in text, "a merged suggestion must still be unable to block"
    assert "never edits" in text, "it has to say it does not touch neti.yaml"


def test_a_set_nothing_can_size_lands_in_a_list_and_not_in_yaml() -> None:
    """The escape valve. Without it a model forces a Slack channel into entra.principals."""
    text = _fragment()
    assert "slack__history /channel_id" in text
    assert "RESOLVER_CONTRACT.md" in text
    assert "resolver: no_shipped_resolver" not in text


def test_nothing_is_suggested_when_the_model_claims_nothing() -> None:
    text = render_fragment([], model="m", provider="p", unsized=[])
    assert "claimed no parameter" in text
