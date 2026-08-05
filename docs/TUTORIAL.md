# neti in three days

A walkthrough, in the order it actually happens. Every command here is real and every number is one
this produced on a real machine — where a number is illustrative, it says so.

The shape of it: **day one you measure, day two you decide, day three you enforce.** That ordering is
not ceremony. A policy asks for a ceiling — `above: 300, verdict: block` — and on day one nobody
knows whether 300 is generous or absurd for their repository. So neti gives you the number first.

---

## Day one — find out what your agent can reach

```console
$ pip install "neti[all]"
$ neti start
```

That is the whole first run. It finds your agent, writes a policy that **blocks nothing**, and
measures the machine you are standing on:

```
3. Measuring this machine
   The largest set one gated call could touch, right now, here:

      1,680 objects

   That is capability, not an incident. Nothing has gone wrong — it is the
   answer to a question nothing else in your stack asks.
```

**What that number is.** The largest set a single gated tool call *could* touch, read straight from
the directory. No traffic needed, nothing observed yet. If your agent can run `Glob`, this is how
wide one `Glob` can be.

**What it is not.** It is not an incident, an alert or a score. Nothing has gone wrong. It is a fact
about your capability surface that nothing else measures — authorization answers *may you*,
sandboxing answers *where*, approval answers *did a human say yes*. None of them answers **how big**.

Now put the gate in front of your agent:

```console
$ neti install
```

That adds a `PreToolUse` hook to `.claude/settings.json` — merged into whatever is there, backed up
first, and it shows you the change before writing. Then work normally. Nothing is blocked. Every
call is sized and recorded.

---

## Day two — look at what it actually did

```console
$ neti report
```

Or, to look at it rather than read it:

```console
$ neti console
```

The overview leads with three numbers — what is reachable, how many decisions were recorded, how
many would have been blocked — and then the part worth the whole exercise:

```
Glob /pattern          n=5 · p50 240 · p95 1,680 · max 1,680    ceiling 50
delete_files /pattern  n=3 · p50 40  · p95 1,400 · max 1,400    ceiling 25
Read /file_path        n=4 · p50 1   · p95 1     · max 1
```

That is your agent's real distribution. Half of its `Glob` calls touched 240 objects; the worst
touched 1,680. `Read` never exceeded 1. **You would set very different ceilings for those two, and
now you can see which** — instead of guessing.

Then let it propose the numbers:

```console
$ neti propose
```

`propose` reads your own recorded traffic and suggests ceilings, with the observed distribution
behind each one. It prints a fragment. It does not edit your policy — you read it, argue with it,
and paste what you accept.

---

## Day three — turn it on

Edit `neti.yaml`: change `mode: observe` to `mode: enforce`, and commit the ceilings you accepted.

From here a call over the line comes back to the agent as a tool *result*, not an exception:

```
Preflight blocked this call: /pattern resolves to 1,680 objects, above the
declared ceiling of 300. Narrow the target and try again.
```

The number is the point. An agent told "denied" gives up or retries the same thing; an agent told
*1,680 against a ceiling of 300* narrows its target. That sentence is identical on every runtime —
fifteen seams are asserted byte-for-byte identical, so the door your agent came through never changes
the answer.

Two things you now have for free:

```console
$ neti verify        # recompute the hash chain over every decision
$ neti inventory     # what each gated tool could reach, ceilings and all
```

`verify` re-derives each link from the stored record. Alter one byte of one decision and it says
`CHAIN BROKEN at decision <id>`.

---

## What this does not do

Written down so you are not surprised later. The full list is in [`SCOPE.md`](../SCOPE.md); the ones
people hit first:

- **Cumulative effect.** It sizes one call. A thousand small calls that add up to something large
  need a declared session budget, not a per-call ceiling.
- **Whether the action was right.** Deleting the one row that mattered is a small call. A counting
  gate cannot see that, and says so.
- **Code-executing agents.** An agent that writes and runs Python calls tools *and* can touch the
  filesystem directly. Wrapping the tool gates the tool; the sandbox is what bounds the rest.

---

## Open source, or hosted

**The local install is not a demo, a trial or a reduced mode.** It resolves real magnitudes off your
real machine, enforces real verdicts, and seals a real hash chain you can re-verify. If you never
install anything else, the gate is doing its whole job.

The rule dividing the two is *"can one machine do this?"* — which is why enforcement is free.

| | open source (Apache-2.0) | hosted |
|---|---|---|
| Resolving what a call will touch | ✅ everything | same engine |
| Blocking a call over a ceiling | ✅ everything | same engine |
| The sealed record chain, and verifying it | ✅ everything | same records |
| Every runtime adapter, every seam | ✅ everything | same adapters |
| The console, on localhost | ✅ everything | — |
| **A `confirm` that reaches a human** | stops the call | routes it to somebody who can answer |
| **One policy across a fleet** | per machine | central |
| **Decisions from many machines in one place** | per machine | central |

A `confirm` band means *somebody other than the agent's operator should decide this one*. On one
machine there is nobody to ask, so the gate stops the call and says so. That is correct behaviour and
a free install keeps doing it forever. What the hosted tier sells is a server that is running — not a
secret about how to decide, which is in this repository and stays there.

---

## Where to go next

| | |
|---|---|
| What it deliberately does not cover | [`SCOPE.md`](../SCOPE.md) |
| Which licence covers what | [`LICENSING.md`](../LICENSING.md) |
| Adding a resolver | [`RESOLVER_CONTRACT.md`](../RESOLVER_CONTRACT.md) |
| The design rules | [`DESIGN.md`](../DESIGN.md) |
