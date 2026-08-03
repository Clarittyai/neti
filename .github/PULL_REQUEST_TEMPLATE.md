<!--
CONTRIBUTING.md is the long version. This is the short one, and it is mostly one question.
-->

## What this changes

<!-- One or two sentences. What was true before, what is true after. -->

## The test that fails without it

<!--
The house rule, and the only one that is non-negotiable:

    A fix with no test that fails without it has not been demonstrated.

Name the test. If you can, revert your change, watch it fail, and say so here — that sentence is
worth more than any amount of description, and it is how every defect in this repository has been
established.

For a change with genuinely no observable behaviour (a comment, a rename), say that instead.
-->

## Claims

<!--
If this PR adds or changes a number, a benchmark, or a sentence about what neti catches:

- a claim about what it catches belongs in `src/neti/eval/incidents.py`, with its source
- a published figure has to be measured, not modelled — `tests/property/test_docs_are_true.py`
  re-measures the ones in the README rather than trusting the table
- a number that moved in the flattering direction deserves a second look before it lands
-->

## Checks

- [ ] `just check` — ruff, format, mypy, the suite
- [ ] `NETI_REQUIRE_SDKS=1 uv run pytest -q` if this touches an adapter, so nothing skips silently
- [ ] `just media` re-run and the SVGs committed, if this changes any command's output
- [ ] `SCOPE.md` updated if this changes what neti does or does not cover
- [ ] `CHANGELOG.md` under `## Unreleased`, if an operator would notice

<!--
Not a checklist item, because it is a judgement: a resolver change should say which direction it
reports and why. `EXACT` may both allow and block; anything capped or estimated must be a
`LOWER_BOUND`, which can block soundly and can never allow. Getting that wrong makes every allow
above it unsound, and it is the one mistake the type system will not catch for you.
-->
