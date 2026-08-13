# Contributing

The fastest way to be useful here is a **resolver**. Everything else in `neti` is one integer
comparison; resolvers are what turn a symbolic argument into the integer, and each new one is a
class of call the gate can suddenly size.

Read [RESOLVER_CONTRACT.md](RESOLVER_CONTRACT.md) first. It is short, and its three rules are the
whole reason the audit claim holds:

1. A resolver returns an **exact count, an upper bound, or "I do not know"** — never a guess.
2. There is no partial resolution. You cannot half-read an integer.
3. Nothing in the decision path reads a clock, an environment variable, or the network twice.

Rule 2 is the one people push back on. A resolver that returns "about 40,000" is worse than one that
returns `UNRESOLVED`, because the first produces a verdict nobody can defend and the second produces
a declared policy decision.

## Setup

You need [`uv`](https://docs.astral.sh/uv/) **0.5 or newer**, and optionally
[`just`](https://github.com/casey/just).

```console
$ just install     # uv venv + editable install with dev extras
$ just test        # the full suite
$ just prop        # determinism, monotonicity, direction soundness, purity — the load-bearing ones
```

Before opening a PR: `just test`, `uv run mypy`, `uv run ruff check`, `uv run ruff format --check`.

**Without `just`**, every recipe is a couple of ordinary commands — `just --list` names them, or read
the `justfile`, which is short. The ones you will want:

```console
$ uv venv --python 3.12                                                  # just install, line 1
$ uv pip install -e '.[dev,cli,graph,mcp,console,sdks,sdks-extended,storage,database]'
$ uv run pytest -q                                                       # just test
$ NETI_REQUIRE_SDKS=1 uv run pytest -q                                   # just test-all
```

`test` and `test-all` differ by that one variable, and it is the difference between eighteen SDK
adapter tests running and quietly `importorskip`-ing themselves out of the summary. CI runs the
second.

**The uv floor is not a preference.** `pyproject.toml` declares `[tool.uv] conflicts`, which is how
the `sdks` and `sdks-semantic-kernel` extras are allowed to want incompatible pydantic versions
without making the project unresolvable. uv 0.4 does not know that key: it warns
`unknown field 'conflicts'` and then fails to resolve with *"your project's requirements are
unsatisfiable"*, naming pydantic rather than the version of uv reading the file. The committed
`uv.lock` masks it until you change a dependency, so the error arrives long after the cause. If you
see that, check `uv --version` before you believe the message.

## What the tests are for

Four suites, and they are not interchangeable:

- `tests/property/` — the invariants. `neti.core` touches nothing outward; the same input gives the
  same digest under any hash seed; the free package never imports the paid one. If one of these
  fails, something about the product's claims stopped being true.
- `tests/integration/` — the seams. MCP over stdio and HTTP, the hook, `Preflight`, the control
  plane. These run real subprocesses and a real server, because the things that break at a seam are
  real-process behaviours.
- `tests/bench/` — the decision is microseconds of pure CPU. **These are timing-sensitive and will
  fail on a cold venv or a loaded machine.** Run them warm.
- `tests/unit/`

## Two rules about what goes in

**No learned thresholds, anywhere.** `neti propose` prints suggestions for a human to edit into
config, and that is the only channel. Nothing computed from observed traffic may reach the decision
path — the moment it does, the product becomes anomaly detection with a worse story.

**This repository is Apache-2.0, all of it.** The control plane is a separate distribution in a
separate repository. `neti` must never import `neti_cloud`, and
`tests/property/test_licence_boundary.py` will tell you if it does. The control plane must never
import the decision machinery either — the gate decides, the server records who said yes — and that
half is asserted over there, where the source it reads lives. See [LICENSING.md](LICENSING.md).

## Claims

If you write a claim about what `neti` catches, it belongs in `src/neti/eval/incidents.py` with a
public source, and `neti score` has to still print the honest coverage number afterwards. The
scorecard reports the misses on purpose. See the "Things we do not say" list in
[SCOPE.md](SCOPE.md).
