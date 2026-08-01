# Changelog

## Unreleased

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

### Still not claimed

`neti score` still reports 3 of 7 on incident coverage. `storage.objects` was built expecting to
close the PocketOS/Railway entry and does not: that was a Railway block volume deleted by ID through
Railway's API, where `ListObjectsV2` has nothing to enumerate. Its proximate cause was also an
unscoped credential, which is upstream of any magnitude gate.

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
