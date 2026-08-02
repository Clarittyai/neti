# Changelog

## Unreleased

### The gate got six times slower the longer you left it on

`neti hook` is one process per tool call, and it read the **entire record file twice** on every one —
once to seed the chain, once again under the append lock. Measured on a lean install:

| records | before | after |
|---|---|---|
| fresh | 133ms | 139ms |
| 10,000 | 273ms | 139ms |
| 50,000 | 816ms | 136ms |

The README published a flat *p50 172ms*, and this page's own advice is to run a week in observe
mode — which is how you get to fifty thousand records. A gate that becomes six times slower the
longer it is installed is a gate people uninstall, and nothing in the suite could see it because
every test writes a handful of records to a fresh temp file.

The head now lives in a `.head` sidecar keyed on the record file's byte length. Anything that
appended, truncated or rewrote the file outside the sink stops the key matching and every reader
falls back to the full walk, so a stale, corrupt or absent sidecar costs a walk and never a wrong
answer — three tests in `test_regressions.py` hold that line, including one that makes the walk
raise to prove the sidecar is genuinely being used.

The cost table now publishes the flat figure and says what used to happen.

### `pip install neti` could not `import neti`

The package's base dependencies are pydantic and pyyaml. `resolvers/graph_client.py` imported
`httpx` — which lives in the `graph` and `mcp` extras — at module scope, and that file is reached by
`engine` -> `registry`, so a plain install raised `ModuleNotFoundError: httpx` on the first line of
the README's own in-process example. The entire public surface, unusable, on the install instruction
most people would try first.

Nothing could have caught it for exactly the reason nothing caught `import fcntl` two releases ago:
every environment the suite runs in installs the extras.

The httpx client is now built on first *use* rather than at construction, so a policy that never
reaches Graph never imports it — and `resolvers_for_client` can keep building a `GraphClient`
unconditionally, which is what makes a mistyped entra resolver fail on its first call instead of at
startup. `test_platform_imports.py` gains the general rule: every module a fresh `import neti`
loads must be importable with the base dependencies alone, plus the other half — the README's
filesystem example reaching a real verdict with no extras at all.

Measured in a clean venv: `pip install neti`, `from neti import Preflight`, a real gated call.

### The published record size was ~50% optimistic

`README.md` said **~700 bytes per call**, under a heading that says *measured, not modelled*. A
coding agent's calls against `examples/coding-agent.yaml` write a median of **1,057 bytes** with
short relative paths and **1,230** with a realistic absolute one. Nothing recent caused it — the
`synthetic` field added this release is five bytes of it — the number had simply not been
re-measured, and it was wrong in the direction that flatters us.

Most of a record is `causes`, the per-argument evidence that makes a verdict re-derivable, plus
whatever `args` the call carried; it moves with how long the operator's paths are. So the table now
publishes a range, and `test_docs_are_true.py` measures records and compares them against the range
it reads out of the README. A project that asks people to check its numbers has to survive its own
numbers being checked.

### The console showed invented magnitudes as measured ones

The third and last layer of the `--demo` defect. The record carries `synthetic`, the API returns it,
and the console rendered neither — so a demo row sat beside a measured one with the same confident
figure and nothing to tell them apart, in the surface built specifically for showing people numbers.
The decisions list now marks the row and the detail page names the source in its provenance block
and says why the marker cannot be stripped.

### `neti prove` — every door, one call, and a chain you can re-check

Eleven adapters is a number in a README. This runs the same call through every seam the machine it
is on can actually reach, prints the verdict, the magnitude and the sentence each one produced, and
verifies the hash chain they wrote into.

Two rules make it worth reading rather than worth skipping.

**Driven and cited are never mixed.** The SDK adapters need SDKs the wheel deliberately does not
ship, so on a bare install some doors cannot be opened. Those seams still appear — silence would
let a reader infer coverage that is not there — but as *not driven here*, naming the import that is
missing and the test that does drive them. A row that looks measured for a door nobody opened is the
one output this command must not be able to produce, and
`test_a_missing_sdk_is_never_rendered_as_a_measurement` is the guard.

**The proof is the chain, not the table.** Any program can print eleven identical lines. Every
decision goes into one real file, and the command prints the `neti verify` invocation that re-checks
it — including `--mode enforce`, because observe and enforce are different policies with different
digests and the obvious form of that command reports every record as "decided under a different
policy". An instruction that does not work is worse than none. `neti verify` grew `--mode` for it,
which an operator whose policy has since moved to enforce needed anyway.

Four doors open with no extras installed, so the demo has a floor on a machine with nothing
configured — which matters, because `neti demo --here` with no traffic yet stops after two of six
acts, and this is the half that needs nothing.

### Every section of the scorecard names its evidence

`eval/README.md` already says *a trial that does not end as a number on `neti score` does not count*.
This is the converse, made mechanical: a number on `neti score` that nothing produced does not count
either. Each section prints the artefact behind it, every cited path must exist, and a section with
no `EVIDENCE` entry fails the build.

It caught one immediately. `NOT YET MEASURED` had no evidence — which is exactly what that section
*is*, so it now says so out loud rather than being the one heading that quietly sits outside the
rule.

### A `--demo` record was indistinguishable from a measured one

`--demo` resolves against the built-in tenant so somebody can watch the whole path work with no
credentials. It produces numbers that are exact, confident and entirely invented — *41,203
principals, `direction: exact`* — sealed into the same hash chain by the same code, and nothing in
the record said so. The default records path is the one a real run writes to.

So a demo interleaves fabricated traffic with measured traffic in one file. `neti report` averages
the two. `neti propose` reads that summary and would have suggested a production ceiling fitted
partly to a fixture. An auditor reading the chain had no way to tell which rows were which.

`DecisionRecord` gains `synthetic`, and the record schema goes to **`neti.decision.v2`** — a version
rather than an additive field, because `chained` is what `verify_chain` recomputes and adding a key
to it unconditionally would make every record written before today report as tampered with. A v1
record recomputes exactly the fields it was sealed over and still verifies; a v2 record covers the
marker, so it cannot be stripped by anyone holding the file. `tests/property/test_record_schema.py`
pins both halves.

Downstream: `neti report` says how many decisions in the window are synthetic, above the numbers
rather than under them. `neti propose` **refuses** such a window — everything it prints is a figure
an operator is about to paste into a policy and defend at 2am — unless `--allow-synthetic` says they
know which it is, which is what the no-credential walkthrough passes.

### A log file that could not be opened switched enforcement off

The worst defect this project has had, and the sibling test that should have caught it had the right
sentence in its docstring the whole time.

`neti hook` reads the record chain's head before it decides, so a records path that is not writable —
a full disk, a permissions change, a path pointing at a directory — raised out of that read and hit
the catch-all handler. That handler exits 0 with nothing on stdout, and **no stdout is how the
`PreToolUse` protocol spells "no opinion"**. Every gated call in the session proceeded. The reason
went to stderr, which in a hook nobody reads.

`test_an_unwritable_records_path_still_lets_the_session_run` said *"Recording is evidence, not the
decision. Losing the ability to write must not become an inability to answer"* — and only ever
checked that the process survived, so it never noticed the answer had gone with the file. For a
product whose claim is "blocks calls whose resolved magnitude exceeds a ceiling you declared", that
is the claim silently retracting itself.

Now: the chain-head read degrades to a fresh chain, `Gatekeeper.decide` catches a failing sink and
carries `record_error` instead of raising, the call is still gated, and the operator is told on
stderr that the audit chain has a gap. `neti verify` reports the break. Pinned at the shared layer —
one test on `Gatekeeper`, which is where all eleven doors write — and end to end through the hook.

**SCOPE.md gains NC-13** for it, because a chain that can have gaps is a thing a customer is entitled
to know before they rely on the record rather than after.

### `ProposedCall.call_id` is gone

Set by the MCP gateway, read by nothing — the same dead-field shape as `providers:` and
`ServerSpec.env`, both of which shipped looking configured and doing nothing. Correlating a decision
to the agent's own tool call is worth having and is not free: the record's digest covers an explicit
field list, so a field outside it is annotation a tamperer can rewrite, and a field inside it changes
what `verify_chain` recomputes — every record written before the change would fail verification.
That is a `neti.decision.v2` with a migration, decided on its own merits. Removed rather than kept
warm.

### Coverage, on both axes, and the four defects that were hiding behind it

Two claims this product rests on were being made by tests that could not check them. The seam table
proved eleven runtimes agree — about Entra, because every case in it used `examples/entra.yaml`. And
`neti init` gated **0 of 160** tools across the MCP servers people actually install, which no fixture
could see because every fixture was a tool somebody here wrote to be gateable.

Fixing the tests found four defects. Every one of them was a thing that had never been pointed at
something it did not already agree with.

- **`Preflight.from_config` demanded Entra credentials for every policy.** The shipped
  `examples/coding-agent.yaml` — every gate `fs.paths`, no credential needed anywhere in it — raised
  `missing credentials: NETI_TENANT_ID…` on the one seam the README hands you for a tool loop you
  wrote yourself. The CLI had already been fixed for exactly this; the predicate was private to
  whichever door noticed first, so it moves onto `Policy`.
- **A pending approval read three different ways across eleven seams.** The hook and the MCP gateway
  each carried their own copy of the sentence and had drifted — the gateway told the model to *retry
  this exact call once it is granted*, which is the entire reason for naming an approval id, and the
  hook did not. `Preflight` had no copy at all, so on the SDK seams a pending approval arrived as a
  flat "needs confirmation": no id, no sign a human had been asked, nothing to retry against. A
  paying customer on LangChain got strictly less than one on MCP. The table had granted and denied
  rows and no pending row.
- **Session budgets could never accumulate on the OpenAI Agents seam.** The adapter passed the SDK's
  `tool_call_id` as `session_id`, and that identifies one invocation, so every call opened its own
  tally and the session total was permanently 1. SCOPE.md NC-01 says only a declared budget sees four
  thousand small calls; on that runtime the budget was inert. Invisible until the test driver stopped
  pinning `tool_call_id` to a constant — a fixture tidier than reality hides the bug reality has.
- **`explain_denial` phrased a CONFIRM-on-unresolved as itself**: "Preflight confirm: /to could not
  be sized". Not cosmetic — four seams hand the model a sentence and no structured payload, so on
  those runtimes the sentence *is* the verdict, and a CONFIRM opening with "confirm" was
  indistinguishable from a block.

### Measured

- **M8 is now a section rather than an outstanding item.** Eleven seams — the hook, both MCP
  transports, the in-process gate, and adapters for Anthropic, OpenAI Agents, LangChain/LangGraph,
  CrewAI, Pydantic AI, AutoGen and Google ADK — driven across five resolver families by one table,
  asserting the same verdict, the same magnitude and the same denial sentence byte for byte. It was
  63 rows; it is 301.
- **M10 moves off zero: 23 of 160 gated, then 25 after the corpus found another rule.** The more
  interesting half is what the matcher now refuses. The hand-written map in `eval/surveys/` claimed
  43 of those tools were sizeable and was wrong about roughly two thirds, always the same way — it
  matched a parameter *name* and asked nothing else. 21 hits on an `owner` belonging to a call that
  touches one issue; 21 on a `repo` that `github.files` structurally cannot take, since a JSON
  pointer resolves one value and `/repo` arrives as `api` and is read as an owner; 7 on a `query`
  that is a web search. Those are declines now, each with its reason written into the generated
  policy, because an operator cannot overrule a judgement they cannot see. Every registered resolver
  is either proposable or has a stated reason it is not, so `never_proposed` is empty.

### Added

- `tests/e2e/worlds.py` — a policy and its resolvers per kind of thing an agent touches. `db.rows`
  reaches a real sqlite file through the shipped `EnvCountRunner`, so the `sqlite:///` parsing and
  the read-only open run for real rather than being bypassed by an injected runner.
- `tests/corpus/` — 170 real tool schemas and the judgement on each, derived from the field survey
  and from Claude Code's own built-ins, which no `tools/list` anywhere reports. `NotebookEdit` fell
  out of it immediately: `notebook_path` is a plain local file on the runtime this gates most often,
  and the rule was an enumeration of the spellings that happened to get written down.
- `just conformance` and `just corpus-refresh`.
- The `sdks-extended` extra. The four new SDKs install cleanly alongside the existing three —
  measured with a real resolve — but their closure is ~200 packages including chromadb and numpy,
  almost all of it via CrewAI, so `just install` does not hand somebody gating a coding agent a
  vector database. CI installs both.

### Known, and deliberately not fixed here

- `ProposedCall.call_id` is set only by the MCP gateway and read by nothing — neither `Engine` nor
  `DecisionRecord`. The same dead-field shape as `providers:` and `ServerSpec.env`. Plumbing it into
  the record changes the record schema and therefore the chain digest, which touches `verify`,
  `replay` and the golden transcripts; it is not being bundled into a coverage change.
- Claude Code's built-in schemas in `tests/corpus/builtins.py` are authored from documentation
  rather than captured from a live session. It is the one part of the corpus with no live source,
  and a parameter renamed upstream would not fail anything until somebody noticed.

### Field trials: what happens when the tests stop writing their own fixtures

Everything in `tests/` drives an agent somebody here wrote. `test_real_mcp_server.py` uses a real
server over a real pipe, but the client is a list of JSON-RPC strings, and the seam-equivalence
table drives the three SDK adapters with synthetic inputs. `eval/` is the tier above that: real
servers, real providers, run by hand rather than by CI. See [eval/README.md](eval/README.md).

The first pass measured coverage and stood up three live provider tiers. It found four defects, all
of them the same shape — a thing that had never been pointed at anything real.

- **`neti init` could not introspect any server that needs a credential.** `find_clients` parsed the
  `env` block out of the client config into `ServerSpec.env`, and nothing ever read it, so every
  credentialed server — Slack, GitHub, Notion, Stripe, Drive — exited at startup naming the variable
  it wanted and was reported as un-introspectable. That is most of the SaaS surface, and the
  operator's own config had the token in it the whole time. The same dead-field failure as
  `providers:`, one directory over. `StdioUpstream` now takes an `env` merged over the inherited one
  — merged, never substituted, or every server that declares any `env` loses `PATH`.
- **`db.rows` never closed its connection.** Deliberate to hold one open — the gate is a long-lived
  process — but nothing closed it, and psycopg's own `__del__` warns about that. Under
  `filterwarnings = error` the warning surfaced on whichever unrelated test happened to trigger the
  collection. Invisible to thirty-nine offline tests because sqlite does not warn.
- **`boto3` and `psycopg` were not installed in a working checkout**, despite both extras being in
  `just install` *and* in the CI line, with a test asserting CI installs them. Nothing asserted they
  *import*, and no offline test needs them: the storage tests drive a mock lister and the database
  tests drive stdlib sqlite. Both are now in `test_no_silent_skips.py`.
- **`uv run mypy` passed or failed depending on which extras were in the venv**, because the two
  deferred provider imports carried inline `type: ignore[import-not-found]` comments that mypy calls
  unused once the package is present. Moved to a `[[tool.mypy.overrides]]` block.

### Measured

- **M10, coverage in the wild.** Against a config carrying the MCP servers people actually install,
  `neti init` gates **0 of 160 discovered tools** across the 13 servers that launch. Forty-three
  carry a parameter a *shipped* resolver could size, so the gap is a matcher defect rather than a
  missing resolver: `insight/discover.match` only knows `entra.principals` and `entra.apps`, and can
  therefore only ever propose 2 of the 10 resolvers that exist. It is the least comfortable number
  in the project and it is now on `neti score`.
- **M11, live provider verification.** `db.rows`, `storage.objects` and `terraform.destroy` all
  shipped without ever touching a real provider. All three now have a live tier that needs no cloud
  account — Postgres and MinIO in Docker, and Terraform's `null` provider — behind `just live-up`.
  The Entra family stays unverified and stays printed as unverified.
- **M7 (what a real model does after a denial) and M8 (harness compatibility) are now listed as
  outstanding** rather than being absent from the card. No LLM has been in the loop in this
  repository, and the claim that a denial makes an agent narrow its scope has never been observed.

### Added

- `examples/data-agent.yaml` and `examples/infra-agent.yaml`. `db.rows`, `storage.objects` and
  `terraform.destroy` all shipped with no example; `test_shipped_examples.py` globs the directory,
  so both are load-and-construct tested.
- `just live-up` / `just live` / `just live-down`, and `just field`.

Coverage of what agents actually run: four more resolvers and a fourth runtime. 0.1.0 could size an
Entra group and a Terraform plan, which meant that pointing it at a coding agent produced a page of
`allow` — the seams worked and there was nothing behind them.

### Resolvers

- **`fs.paths`** — files under a path, directory or glob. Exact, local, strongly consistent; capped,
  and a capped answer is a `LOWER_BOUND` rather than a small number. This is the one that makes
  gating a coding agent mean anything.
- **`db.rows`** — rows a `DELETE` or `UPDATE` would take, counted with `select count(*)` rather than
  `EXPLAIN`, so the low bias of planner estimates never applies. Always a `LOWER_BOUND`, because
  `ON DELETE CASCADE` fan-out is invisible to it. It recognises two statement shapes and declines
  everything else: a mis-parse yields a count of the wrong predicate and a confident wrong verdict,
  which is worse than no answer. `SCOPE.md` NC-10 is rewritten to say exactly what is now covered.
- **`storage.objects`** — objects and bytes under an `s3://bucket/prefix`. The only resolver here
  that is not O(1), because object stores have no prefix-granularity count; capped hard, and past
  the cap a `LOWER_BOUND`.
- **`github.repos` / `github.files`** — every repository in an org (one request), and every file on
  a repository's default branch (one recursive tree request, with GitHub's own `truncated` flag
  becoming the direction). New `repositories` unit, because "block above 50" is a sentence somebody
  can defend about files and a very different one about repositories.

Each declines rather than guessing, and none can return `0` for something it could not reach.

### Runtimes

- **LangChain and LangGraph**, via a delegating `BaseTool` — `bind_tools`, `create_react_agent` and
  `ToolNode` all accept it, and `convert_to_openai_tool` produces a byte-identical schema, so an
  agent cannot tell a gated tool from an ungated one.
- Alongside the Anthropic `tool_runner` and OpenAI Agents SDK adapters. All three return a denial as
  a tool *result*, never an exception: a run that has died cannot narrow its scope and try again.

### Fixed

- **A policy that gated no Entra tool still demanded `NETI_TENANT_ID`.** Every command built the
  full directory registry before reading the policy, so gating file writes required an Azure app
  registration. Credentials are now required only by the resolvers a policy actually binds — and a
  policy that *does* bind one still fails loudly at startup rather than on the hot path.
- **A blocked call killed a LangGraph run.** `ToolNode` requires a `ToolMessage`; the denial was a
  bare string, so it raised `TypeError` inside the node. Every direct-`invoke` test passed against
  that. A gate that crashes the agent is worse than the call it stopped.
- **`sqlite:////absolute/path` was read as a relative path**, and because `sqlite3.connect` creates
  a missing file, the symptom was an empty database in the working directory rather than an error.
  Now read-only, and absent files say so.

### `neti propose` reports the interrupts its ceilings cannot remove

The IMPACT line used to be computed by comparing bare magnitudes, which made it wrong for every
resolver that reports a bound. `decide` escalates any resolution that cannot soundly clear a
ceiling, so with `db.rows`, `storage.objects`, a capped `fs.paths` or `github.files`, a call
*under* the ceiling is still an interrupt. On real traffic the old output read "nothing in the
observed window would have been stopped" when in fact all forty calls escalate.

There is now a second line reporting those separately, because no choice of ceiling changes them:

```
  IMPACT    over the observed window this would have blocked 4 call(s) and asked about 0
  ALSO      36 call(s) under these ceilings resolved to a bound rather than a count
            (cascades, caps, members you cannot see). These take your declared
            on_unbounded / on_unresolved verdict whatever ceiling you pick.
```

### Credentials no longer reach the audit log

The worst-shaped defect a security tool can have, and it was there from the first release: every
gated call recorded its arguments verbatim, so a tool called with `{"api_key": "sk-live-…"}` wrote
that key in plaintext into the file this product asks people to keep, verify and hand to an auditor.
The file was also mode 0644 — on a shared host, an audit log of every agent's every tool call,
readable by anyone.

Both fixed. `core/redact.py` replaces credential-shaped values, tested against real formats (GitHub,
OpenAI, Anthropic, Slack, AWS, JWTs, PEM keys, connection strings with inline passwords) by both key
name and value shape, since either test alone is too weak. Records are `0600`.

**The gated target is never redacted** — that is the rule everything else bends around. It is the
evidence the verdict was measured from, `causes` carries it regardless, and redacting it would
protect nothing while making the record useless. What was redacted is *named* in the record and is
inside the hash digest, so a hidden field cannot be made to look like an absent one.

### `neti install` — one command instead of hand-edited JSON

Wiring the gate meant editing `.claude/settings.json` to a shape you had to copy correctly, in a
file an agent depends on, with no feedback until the next session behaved oddly. Now it merges into
whatever is already there, leaves other hooks and every other key untouched, is idempotent, prints
the change before making it, and keeps a backup.

It refuses in two places rather than guessing: settings that cannot be parsed are never overwritten,
and a policy is *constructed* rather than merely parsed before being wired in.

### `neti demo --here` — a finding about your machine, not a fixture

The existing demo is careful to say what it is: *"It demonstrates behaviour, not a finding."* True,
and also why it cannot answer the only question an evaluator asks — what this would find in *their*
environment.

`--here` runs the same six acts against the directory it is standing in, through the same engine,
decision procedure, records and reports as production. Acts 1 and 2 need nothing at all and produce
the day-one number: *an agent working here reaches N objects*. With traffic it runs the rest —
report, propose, enforce the proposed ceilings against the same calls, verify and replay.

Two supporting pieces:

- **`providers:` is finally read.** It had been in the policy schema since the first release and no
  code path ever looked at it, so `fs.paths` had no root, `github.repos` no owner, and
  `neti inventory` could only produce a number for Entra. A fourth construction guard now refuses
  any provider key nobody reads, beside the three that already catch dead config.
- **`examples/coding-agent.yaml`** — the policy for the agent most people actually run. `Bash` is
  ungated in it deliberately, with the reasoning next to the gap.

`neti.eval.corpus` captures a decision log into re-runnable traffic: the hook already records every
call, so an afternoon in observe mode *is* the capture. Paths are kept relative to the repository
root and anything outside is dropped rather than scrubbed, because a corpus is a thing people share.

Two things it will not do: attribute a resolver's reach to a single tool (`Edit` takes one
`file_path` and touches one file), and print a capped walk as a total — a 712,359-file tree reports
`≥ 200,000`, not `200,000`.

### `neti verify --config` replays the log against the decision procedure

The command has always said *"Replay every decision and verify the hash chain"* and only ever did
the second half. They answer different questions, and the second is the one the architecture is
arranged around — resolvers do the I/O, `decide` is pure, and a record keeps the resolutions
precisely so the decision can be re-run:

- **the chain** answers "has this record been altered?"
- **replay** answers "does this verdict still follow from this evidence?"

Give `verify` a policy and it re-derives every recorded verdict from the stored magnitudes,
directions and ceilings. Concretely: upgrade `neti`, replay a year of audit log, and find out
whether anything would now be decided differently. Records written under a different policy digest
are reported rather than silently skipped. Without `--config` the command behaves exactly as before.

The distinction is sharp enough to test: **tampering breaks the chain, and a decision-procedure
regression does not.** A change to `decide` leaves every digest valid and every verdict wrong, which
is the failure an auditor is relying on this tool to notice.

### End-to-end coverage, and the four defects it found

A new `tests/e2e/` tier tests the product rather than its parts: one invariant over all seven
integration seams, the operator's first week as one flow, every resolver driven through record and
report and back, the hook fuzzed for crash-safety, and `neti gate --stdio` in front of a real
`@modelcontextprotocol/server-filesystem`. It found:

- **An `mcp__server__` prefix bypass.** Tool-name normalisation lived in the adapters, so a
  federated name matched the policy through the hook and the three SDKs but fell through as unknown
  over MCP and through `Preflight`. `Policy.match_tool` now matches exact-first, then
  prefix-stripped — so per-server entries still win, and a proxy's renamed tool no longer slips a
  gate somebody wrote.
- **A resolver that raised took the process with it.** An 80,000-character argument reached httpx
  as a URL and raised `InvalidURL`, exiting the hook with 1 — which fails *every* subsequent tool
  call in that Claude Code session. The engine now contains any exception from a resolver and routes
  it through the declared `on_unresolved`.
- **CI never ran the three SDK adapters** (below), and **`propose` mispredicted its own impact**
  (above).

### Verified against the live GitHub API

`tests/live/` runs the GitHub resolvers against api.github.com and is skipped without a token
(`NETI_GITHUB_TOKEN=$(gh auth token)`). Counts cross-checked independently: 96 repositories, 1,291
files. The truncation path fires on a real repository rather than only a synthetic one. Fifteen
offline tests passed while three defects were live:

- **`/orgs/{owner}` is a 404 for a person**, so `torvalds` — twelve repositories — resolved
  UNRESOLVED. Falls back to `/users/{owner}`.
- **A wrong `EXACT`.** `total_private_repos` comes back `null` when the token cannot see inside the
  account, so a real org resolved to `EXACT 96` with its private repositories invisible. Now a
  `LOWER_BOUND` unless the count is known complete.
- **`github.files` needs seconds.** torvalds/linux measured 5,684ms against a claimed 800ms budget;
  registered with a ten-second timeout, and the docstring carries the measured numbers instead of
  the claim.

`neti check --demo` now exists, so the Entra tenant check can be run end to end on this machine.
It proves the command works; it proves nothing about any real directory.

### Still not claimed

`neti score` still reports 3 of 7 on incident coverage. `storage.objects` was built expecting to
close the PocketOS/Railway entry and does not: that was a Railway block volume deleted by ID through
Railway's API, where `ListObjectsV2` has nothing to enumerate. Its proximate cause was also an
unscoped credential, which is upstream of any magnitude gate.

**The Entra claims remain unverified against a real tenant.** Risk R2 — that
`$filter=userType eq 'Guest'` is O(1) on the cast `transitiveMembers` collection — and metric M2,
latency, both need a directory nobody here has. `neti check` answers both in one command and is
now itself tested; the tenant is the only thing missing. `neti score` says so.

## 0.1.0 — first release

`neti` resolves what an agent's tool call will actually touch, before it runs, and stops it when
that exceeds a ceiling you declared. Two distributions: `neti` (Apache-2.0) is the whole gate;
`neti-cloud` (BUSL-1.1) is the control plane. See [LICENSING.md](LICENSING.md) for where the line
sits and why.

### The gate

- **Three integration seams**, none of which need the agent changed. MCP over stdio — the transport
  every local server in Claude Code, Claude Desktop and Cursor actually uses — and over HTTP; the
  Claude Code `PreToolUse` hook, for the built-in tools no proxy can see; and `Preflight`, for a
  tool loop you wrote yourself. All three produce the same denial, word for word.
- **`neti init`** reads the MCP client configs already on the machine, asks each server what tools
  it exposes, and writes a policy. It declares no ceilings — those come from your traffic a week
  later, via `neti report` and `neti propose`.
- **`neti inventory`** is the day-one finding, with no traffic and no configuration: *this agent
  holds a credential that can, in one call, reach 52,400 people and 214 applications.*
- **`neti console`** — the API and the seven-screen UI, one process, one port.
- **Observe and enforce are both free.** Observe records the same verdict and forwards anyway,
  which is what makes installing it reversible.
- Every decision is sealed into a hash chain that `neti verify` checks offline, forever.

### Approvals (`neti-cloud`)

`CONFIRM` means *a person other than the agent's operator should decide this one*, and on one
machine there is nobody to ask. The control plane is the somewhere that question goes.

A grant authorises **one execution of one call under one policy**: bound to a digest over the tool,
its arguments and the policy; single-use; expiring; and refused if the target has grown past the
number the human actually saw. That last one closes a real TOCTOU window — approve 40 people at
17:00, the group is nested into overnight, and the grant would otherwise execute against 40,000.

If the control plane is unreachable, absent or unpaid, the gate behaves exactly as the free tier.
A control plane can only ever make a decision *more* permissive, and only through a named human.

Notification via the console inbox, a signed webhook, Slack (Socket Mode — no public callback URL),
or email. A notifier can never fail a call.

### Known limitations

Read [SCOPE.md](SCOPE.md); the non-coverage list is numbered and `neti score` prints it. The
headline: `neti` answers *how big*, not *whether this is a good idea*, and a per-call gate cannot
see 4,000 individual sends unless you declare a session budget. `neti score` reports honest incident
coverage of 3 of 7 and names the four it misses.

Session budgets are per-process in this release, so a restart mid-session resets the cumulative
total — the mitigation for NC-01 quietly stops applying. Org-wide policy distribution, fleet audit
and shared budgets are designed but not built.

The control plane is a POC: one shared organisation key, no SSO, no per-user identity beyond the
name a reviewer types. Do not expose it to the public internet.

### Verified against real agents

Before release the gate was put in front of things nobody here wrote: real headless Claude Code
sessions via the `PreToolUse` hook, and `@modelcontextprotocol/server-filesystem` over stdio. Both
passed — no broken sessions, no wrong denials, chain intact — and both found defects no offline test
could have:

- **The record chain forked under concurrency.** Claude Code runs tool calls in parallel; as a hook,
  each call is its own process; two read the same chain head and both appended from it. `neti verify`
  correctly reported a break on a chain nobody had tampered with. Chaining now belongs to the sink,
  under an exclusive file lock — not a thread lock, which would have looked correct and prevented
  nothing. Fixing it also cut tail latency from p95 650ms to 184ms, because the contention was the
  same problem.
- **`neti init` told an operator who had already gated every server that it found none.** It was
  skipping wrapped servers correctly and then reporting as if the config were empty.
- **A server's startup banner made a working scan look broken**, interleaving npm warnings into
  `init`'s output. Relaying a server's stderr while gating it is right; during discovery it is noise.

### Fixed before release

- **The policy digest was not stable across processes.** `BudgetRule.tools` is a frozenset, which
  serialises in hash order, so the same policy file hashed differently under different
  `PYTHONHASHSEED`s. That digest is stamped into every decision record and binds every approval.
- **The record chain broke across restarts.** A new `Engine` appending to an existing file wrote
  `prev_digest: null`, which `verify_chain` correctly called a break — on a chain that was not
  broken.
- **A session budget selected the mildest breached band**, so `confirm above 200, block above 1000`
  returned CONFIRM at 1001.
- **A `send_email` budget declared in `recipients` never fired**, because the gate bound
  `entra.principals` and budgets aggregate by unit. The engine now refuses such a policy at
  construction.
- **`neti propose` anchored on p99**, which with three outliers in 143 calls proposed a ceiling that
  would not have caught the 41,203-person send. Re-anchored on p95.
