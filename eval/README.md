# `eval/` — the field trials

Everything in `tests/` drives a hand-written agent. `tests/e2e/test_real_mcp_server.py` uses a real
server over a real pipe, but the client is a list of JSON-RPC strings; `tests/e2e/test_seam_equivalence.py`
drives the three SDK adapters with synthetic tool inputs. No LLM has ever been in the loop in this
repository.

That matters because the only real-agent contact this project has had — a manual, observe-mode
exercise before release, recorded in `CHANGELOG.md` — produced defects the offline suite structurally
could not: the record chain forked under Claude Code's parallel tool calls, `neti init` reported "no
servers" to an operator who had gated all of theirs, and one live GitHub run produced three defects
including a *wrong* `EXACT`. Highest defect yield per hour of any tier, and the only one that was not
repeatable.

This directory makes it repeatable.

## Why here and not `src/neti/eval/`

`src/neti/eval/` ships in the wheel. It must stay offline, dependency-light and deterministic — it is
where `neti score`, `neti demo` and `neti check` live. The field harness needs Node, Docker, network
and sometimes an API key, it is non-deterministic, and it writes artifacts. Different constraints,
different directory.

## What is here

| path | what it produces |
|---|---|
| `surveys/mcp_coverage.py` | **M10** — of the MCP servers people actually install, how many tools can `neti` size? |
| `results/` | one JSON per survey or trial run. This is the evidence, and it is committed. |

More arrives with later phases: `harness/` (the real-agent driver and the M7 denial-response
taxonomy), `scenarios/`, `fixtures/`.

## Running them

```console
uv run python -m eval.surveys.mcp_coverage            # M10, writes eval/results/mcp_coverage.json
uv run python -m eval.surveys.mcp_coverage --markdown # the table, for pasting
```

**These never run in CI.** They are non-deterministic, they need the network, some of them cost
tokens, and a flaky red build teaches people to ignore red builds. They run under `just field` and
before a release.

## The rule

A trial that does not end as a number on `neti score` does not count, and a measurement that needs an
environment we do not have is listed as absent rather than estimated. That is the same rule
`src/neti/eval/scorecard.py` already applies to M2 and R2; these results follow it.
