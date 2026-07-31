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
