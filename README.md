# neti

**A preflight gate for agent tool calls.** Before an agent acts, `neti` resolves what the action will
actually touch, and blocks it when that is bigger than you said it should be.

```
remove_group_members(group: "engineering-all")
```

Your policy engine sees a string on an allowlist. `neti` sees **412 people losing access to 9
applications** — and stops the call, because you declared a ceiling of 200.

## Why

An agent asks to do one thing. That one thing turns out to be a million things. Nobody finds out
until after. Alignment, authorization, provenance, sandboxing, anomaly detection and rollback all
answer a different question; none of them answers *how big is this*.

## The first hour

Two commands, from a directory with nothing in it. No YAML to write, no traffic to wait for, and no
ceilings to guess at.

```console
$ neti init
Found 1 MCP server(s):
  entra    npx -y @acme/entra-mcp     (Claude Code (project))

Asking each one what tools it exposes…

  gated   delete_group           /group → entra.principals, /group#apps → entra.apps
  gated   remove_group_members   /group → entra.principals, /group#apps → entra.apps
  gated   send_email             /to → entra.principals
  ungated search_directory       nothing here can be sized

Wrote neti.yaml
  3 tool(s) gated, every ceiling left blank on purpose.
```

It reads the MCP client configs already on the machine, launches each server the way its client
does, asks `tools/list`, and writes a policy matching the tools it found. It declares **no ceilings**
— every band is empty and the mode is `observe`, so nothing can be blocked. Those numbers are meant
to arrive a week later out of your own traffic; a ceiling nobody chose is a ceiling nobody will
defend the first time it fires.

Then, still with no traffic:

```console
$ neti inventory
tool                  param        resolver          max reachable  risk
remove_group_members  /group       entra.principals         52,400  no ceiling declared — up to 52,400 principals in one call
send_email            /to          entra.principals         52,400  no ceiling declared — up to 52,400 principals in one call
remove_group_members  /group#apps  entra.apps                  214  no ceiling declared — up to 214 apps in one call

5 of 5 gated parameters have no ceiling declared. They resolve and record, but they cannot block.
```

That is the finding, on day one: *this agent holds a credential that can, in one call, reach 52,400
people and 214 applications, and nothing today would stop it.*

Add `--demo` to `neti inventory` or `neti gate` to run the whole path against a synthetic tenant with
no credentials at all — same engine, same records, only the numbers differ.

## Install

Three steps, no code change.

1. Register an Entra app and grant one permission — `GroupMember.Read.All`, Microsoft's documented
   least-privilege choice for the count endpoint. Admin-consent it.
2. `neti init`, then put the command it prints into your client's config.
3. Nothing else. Uninstall is reverting that one line.

The default mode is `observe`: a pass-through that resolves and records and **cannot block
anything**. The worst case of installing it is one hop.

## Putting it in front of an agent

No SDK, no code change, no redeploy — a config edit in the client you already use. The agent never
learns that the gate exists; it learns that a call was too big, which it already knows how to handle.

**An MCP server** (`.mcp.json`, `claude_desktop_config.json`, `~/.cursor/mcp.json`). Whatever command
launched the server becomes an argument to the gate:

```diff
   "entra": {
-    "command": "npx",
-    "args": ["-y", "@acme/entra-mcp"]
+    "command": "neti",
+    "args": ["gate", "--stdio", "--", "npx", "-y", "@acme/entra-mcp"]
   }
```

**The harness's own built-in tools**, which no proxy can see. Claude Code's `PreToolUse` hook is the
only seam that exists for those, and its contract maps onto the verdict lattice directly —
BLOCK → `deny`, CONFIRM → `ask`, and a pass says *nothing at all*, so the permission rules you
already configured keep working exactly as they were. In `.claude/settings.json`:

```json
{"hooks": {"PreToolUse": [{"matcher": "*",
  "hooks": [{"type": "command", "command": "neti hook"}]}]}}
```

**A tool loop you wrote yourself** — an Anthropic or OpenAI function-calling loop, a LangChain tool,
anything that speaks neither MCP nor a hook protocol:

```python
from neti import Preflight

pf = Preflight.from_config("neti.yaml")

for block in message.content:
    if block.type == "tool_use":
        out = pf.dispatch(block.name, block.input, lambda: TOOLS[block.name](**block.input))
```

`dispatch` returns the tool's own return value when the call fits and the denial *sentence* when it
does not, because your next line hands that string back to the model — and reading a specific number
is what makes it retry with a narrower scope instead of giving up. `pf.check(...)` and `@pf.guard`
are the same decision in the other two shapes. This is the one seam you can forget to use, and
nothing detects the omission; the other two cannot be bypassed that way, which is why they come
first.

**A remote MCP server**: `neti gate --upstream https://mcp.internal/rpc`, then point the client at
the gate instead.

Add `--demo` to either command to rehearse the whole thing against the synthetic tenant with no
credentials at all. Same engine, same decision procedure, same records — only the numbers differ.

## The first week

```console
$ neti report --since 7d
send_email /to        n=1,284   p50=3   p95=41   p99=112   max=8,900
  ▸ 4 calls exceeded 500 recipients

$ neti propose
send_email /to:  confirm above 150   block above 1,000     # 2× observed p99
```

You edit the numbers and commit them. `neti propose` is a config-authoring aid read by a human —
nothing learned ever reaches the decision path, so the gate stays a static integer comparison.

## What it costs to run

Measured against real Claude Code sessions, not modelled:

| | |
|---|---|
| Hook overhead, per tool call | **p50 172ms · p95 184ms** |
| Records | **~700 bytes per call**, so roughly 0.7 MB per thousand |
| Decision itself | microseconds — the overhead above is almost entirely Python interpreter start |

That last row is the one to act on. As a `PreToolUse` hook with `matcher: "*"`, neti starts a fresh
process for *every* tool call, and interpreter start dominates a sub-millisecond decision. If ~170ms
on every call is too much, narrow the matcher to the tools you actually gate:

```json
{"hooks": {"PreToolUse": [{"matcher": "Bash|Write|Edit",
  "hooks": [{"type": "command", "command": "neti hook"}]}]}}
```

The MCP paths do not pay this: `neti gate` is one long-lived process, so the decision is the
microseconds it measures.

**Concurrent agents are safe.** Claude Code runs tool calls in parallel and each hook invocation is
its own process, so several writers can reach the record file at once. Appends take an exclusive
lock and re-read the chain head under it — many processes, one file, no forked chain. There is a
test that runs eight real subprocesses and asserts exactly that, because a single-writer test cannot
see this and an earlier version of the sink forked the chain the first time a real agent ran two
tools at once.

## When a `confirm` needs an actual human

A `confirm` band means *somebody other than the agent's operator should decide this one*. On one
machine there is nobody to ask, so the gate stops the call and says so. That is correct, and it is
what a free install will keep doing.

The paid tier — [`neti-cloud`](cloud/), BUSL-1.1 — is the somewhere the question can go:

```console
$ neti-cloud serve --key $KEY                        # the control plane
$ neti login --url http://localhost:8730 --key $KEY  # on the agent's machine
$ neti gate --stdio --org -- npx -y @acme/entra-mcp
```

The agent's call stops with *"approval a_b271… is pending; retry this exact call once it is
granted."* A reviewer opens the console and sees **500 recipients**, above a ceiling of 50, and
approves. The agent's retry proceeds. The grant is bound to that exact call under that exact policy,
is single-use, expires, and is refused if the target has grown since a human looked at it.

**If the control plane is unreachable, absent, or unpaid, the gate behaves exactly as the free
tier.** A control plane can only ever make a decision *more* permissive, and only through a named
human — so nothing about paying adds availability risk to enforcement. That is a test, not a promise.

| Free — Apache-2.0 | Paid — BUSL-1.1 |
|---|---|
| the engine, all three seams, **observe and enforce** | a second human approving a `confirm` |
| `init` · `inventory` · `report` · `propose` · `verify` · `score` | org policy, one version across the fleet |
| the record chain and `neti verify` | session budgets that survive a restart |
| the console, every screen | audit across every agent |

The rule is *"can one machine do this?"* — which is why enforcement is free and why every paid
feature is a hole [SCOPE.md](SCOPE.md) already documents. See [LICENSING.md](LICENSING.md); the
boundary is enforced by a test, not by good intentions.

## What it does not do

Read [SCOPE.md](SCOPE.md). It is short, it is honest, and the non-coverage list is numbered so tests
and write-ups can cite it. The headline: `neti` answers *how big*, not *whether this is a good idea*,
and a per-call gate cannot see 4,000 individual sends unless you declare a session budget.

## Documents

| file | what it fixes in place |
|---|---|
| [SCOPE.md](SCOPE.md) | what is and is not covered, and the sentences we do not say |
| [DECISION.md](DECISION.md) | the verdict lattice and the decision procedure, one page |
| [RESOLVER_CONTRACT.md](RESOLVER_CONTRACT.md) | the resolver spec — this is the actual product |

## Development

```console
uv venv --python 3.12 && uv pip install -e '.[dev,cli,graph,mcp]'
uv run pytest
uv run mypy
uv run ruff check
```
