"""The answer key Arm C scores against is an opinion, so the opinion has to be checkable.

Arms A and B of M12 are scored against `tests/corpus/decisions.json` — the rule table's own output,
which every other test in this repository already guards. Arm C is different: its key is a reading
somebody wrote down, and a wrong key does not fail loudly. It quietly marks a model wrong for an
answer nobody checked, and the number still looks like a measurement.

Nothing here says the reading is *correct* — no test can. What these say is that it is
well-formed: it covers exactly the parameters it claims to, every rule in it moves at least one
real parameter, no parameter is caught by two rules, and the file on disk is what the script
produces. Those are the failure modes that would make the key silently wrong rather than
arguably wrong.
"""

from __future__ import annotations

import json

import pytest

from eval.answers import adjudicate

LABELS = {"fs.paths", adjudicate.NO_RESOLVER, adjudicate.NOT_A_SET, adjudicate.UNADJUDICATED}

RULES = {
    "A_LOCAL_FILE_TO_WRITE": adjudicate.A_LOCAL_FILE_TO_WRITE,
    "AN_ARRAY_OF_THINGS_THE_CALL_ACTS_ON": adjudicate.AN_ARRAY_OF_THINGS_THE_CALL_ACTS_ON,
    "AN_AUDIENCE_OR_A_HISTORY": adjudicate.AN_AUDIENCE_OR_A_HISTORY,
    "A_QUERY_THAT_RETURNS_A_SET": adjudicate.A_QUERY_THAT_RETURNS_A_SET,
    "A_CONTAINER_THAT_FANS_OUT": adjudicate.A_CONTAINER_THAT_FANS_OUT,
    "I_CANNOT_TELL": adjudicate.I_CANNOT_TELL,
}


@pytest.mark.parametrize("name", sorted(RULES))
def test_every_rule_names_a_parameter_that_exists(name: str) -> None:
    """A typo in a rule is invisible: the pair matches nothing and the parameter falls through.

    `("browser_take_screenshot", "fileName")` would silently be adjudicated `not_a_set`, and a model
    that correctly answered `fs.paths` would be counted as a false claim — the worst kind of wrong,
    because it inverts the sign of the safety number Arm C exists to report.
    """
    real = set(adjudicate.unclaimed())
    ghosts = sorted(RULES[name] - real)
    assert not ghosts, (
        f"{name} names {len(ghosts)} pair(s) that are not among the parameters no rule claims, "
        f"so they adjudicate nothing: {ghosts}"
    )


def test_no_parameter_is_caught_by_two_rules() -> None:
    """`adjudicate` returns on the first match, so an overlap is a rule that never fires."""
    seen: dict[tuple[str, str], str] = {}
    clashes = []
    for name, rule in sorted(RULES.items()):
        for pair in rule:
            if pair in seen:
                clashes.append(f"{pair} is in both {seen[pair]} and {name}")
            seen[pair] = name
    assert not clashes, clashes


def test_the_key_covers_every_unclaimed_parameter_and_nothing_else() -> None:
    key = adjudicate.expected()["labels"]
    labelled = {(tool, param) for tool, params in key.items() for param in params}
    assert labelled == set(adjudicate.unclaimed())


def test_every_label_is_one_of_the_four() -> None:
    for tool, params in adjudicate.expected()["labels"].items():
        for param, entry in params.items():
            assert entry["label"] in LABELS, f"{tool}/{param} carries {entry['label']!r}"
            assert entry["why"].strip(), f"{tool}/{param} has no reason"


def test_the_committed_key_is_what_the_script_produces() -> None:
    """Otherwise the reviewable artefact and the scored artefact are two different files."""
    assert adjudicate.TARGET.exists(), "Run `python -m eval.answers.adjudicate`."
    on_disk = json.loads(adjudicate.TARGET.read_text(encoding="utf-8"))
    assert on_disk == adjudicate.expected(), (
        "eval/answers/claimable.json is stale. Run `python -m eval.answers.adjudicate` and read "
        "the diff — every line of it is a judgement."
    )


def test_something_is_left_unadjudicated() -> None:
    """A key with no unknowns in it is a key that stopped asking.

    Four hundred parameters, a schema line each, and total confidence is not a result — it is a
    sign that the ambiguous cases were rounded toward whichever label was easier to write.
    """
    counts = adjudicate.expected()["counts"]
    assert counts.get(adjudicate.UNADJUDICATED, 0) > 0


def test_the_claimable_ones_are_a_small_minority() -> None:
    """The prior this whole feature rests on, asserted rather than assumed.

    `neti suggest` is worth shipping only if most of the 401 really are page sizes and cursors. If
    a reading ever concluded that hundreds of them are sets, the honest response is to doubt the
    reading — and to notice before shipping a number that says a model missed three hundred gates.
    """
    counts = adjudicate.expected()["counts"]
    total = sum(counts.values())
    sets = total - counts.get(adjudicate.NOT_A_SET, 0) - counts.get(adjudicate.UNADJUDICATED, 0)
    assert sets / total < 0.25, f"{sets} of {total} adjudicated as naming a set"
