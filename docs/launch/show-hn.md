# Show HN draft

Not published. Copy from here when you decide to post; nothing in this repository posts anything.

The title and the first paragraph do all the work on HN, and the failure mode of a launch post for
this particular project would be sounding like the security vendors `POSITION.md` spends four
thousand words distinguishing neti from. So the draft below leads with a number and a mechanism, and
puts the limits in the body rather than in a footnote — partly because it is true, and partly
because on that site an unhedged claim gets found within the hour and the top comment becomes the
thing everybody reads instead of the post.

---

## Title

    Show HN: neti – count what an agent's tool call will touch, before it runs

Alternatives, weaker and why:

- *"A preflight gate for AI agent tool calls"* — accurate, and it could be any of thirty products.
  It names the category rather than the difference.
- *"Stop your AI agent from deleting 41,203 users"* — better hook, worse fit. It promises damage
  prevention, and neti does not contain damage or undo it; SCOPE.md says so in as many words. The
  first commenter who reads that file will quote it back.
- *"neti – a magnitude gate for agents"* — "magnitude" is the right word and nobody knows it yet.
  Save it for the body where there is room to define it.

## Body

Your agent asks to remove one group. The group has 41,203 members.

Your permission system sees `remove_group_members` on an allowlist and the name is allowed — it
answers *whether*, never *how many*. neti resolves the argument to a count before the call runs,
compares that count to a ceiling you wrote down, and stops it if it does not fit.

The stopped call comes back as an ordinary tool result with the number in it:

    Preflight blocked this call: /group resolves to 41,203 principals, above the
    declared ceiling of 200. Narrow the target and try again.

That sentence is the design. An agent handed "denied" gives up or routes around; an agent handed a
number narrows the target and retries. Nothing about the gate reaches the prompt, so the model is
not being asked to cooperate with its own restriction.

Five things are declared, and a call is stopped if any of them says so: **magnitude** (how big is
the set this argument addresses), **sensitivity** (`.env` is one file and still off limits),
**location** (outside the tree you pointed the agent at), **accumulation** (the session total, not
any one call), **provenance** (this session already read a ticket a stranger wrote).

All five are declared, not learned. Every verdict is a static integer comparison against a number a
human committed — no model in the decision path, nothing scored or inferred, nothing served from
anybody's cloud. That is a product decision rather than a technical one: a false block is then
always *"the rule you wrote is too tight"*, which is readable in five seconds and fixable in one
line, instead of *"the model found it suspicious"*, which is how a control gets switched off in
month two.

It goes in front of an agent without a code change — MCP over stdio or HTTP, the Claude Code hook,
or eleven SDK adapters, all asserted to produce the same verdict byte for byte.

**What it does not do**, because you will ask and the repo answers first: it does not know whether
an action is *correct* — a single-row delete of the one row that mattered is invisible unless a
sensitivity rule happens to name it. It does not establish authorization; that is upstream. It does
not contain damage or undo it. Resolution is eventually consistent, so there is a window where a
verdict is provably wrong, and every decision carries `resolved_at` and a consistency class rather
than a freshness claim. Where it cannot resolve, it does not guess — `UNRESOLVED` and `PARTIAL` are
first-class states routed to a fail-closed policy you declare. `SCOPE.md` in the repo numbers
sixteen of these so they can be cited, and they are published whether or not they flatter.

The honest coverage number: a magnitude ceiling only exists where a resolver exists. There are
eleven, over files, shell targets, database rows, object-store prefixes, repositories, principals,
apps and infrastructure resources. A seam without a resolver is a place to write "allow", so that
list is the real measure and adding one is about eighty lines.

You can see the whole thing on your own machine with no credentials and no traffic:

    pip install neti
    neti demo --here

Six acts against your own files — what an agent here can reach, what it did, the ceilings that
follow from that, the same calls re-run with the ceilings on, and a chain that re-derives every
verdict offline.

Apache-2.0, all of it. The hosted tier exists for the one thing a single machine cannot do — route
a confirm to an actual human — and the gate is complete without it.

<https://github.com/Clarittyai/neti>

---

## Notes for whoever posts this

**Time it for a weekday morning US Eastern.** Then stay at the keyboard for three hours; on Show HN
the comments are the post.

**Answers worth having ready**, because they will be asked and the first response sets the tone:

- *"Isn't this just rate limiting?"* — A rate limit counts calls. This counts what one call
  addresses. `remove_group_members` is one call either way, whether the group has three members or
  41,203.
- *"Why not have an LLM judge it?"* — Then the answer to "why was this blocked" is a model's
  opinion, and the operator cannot fix it in one line. It is also a second model in the path of the
  first one. `eval/` has the arm that tested whether a model can recover the gates the rule table
  already makes; the numbers are published.
- *"What if the resolver is wrong?"* — It reports a lower bound when it is capped or estimated, so
  it can block safely and can never allow on a guess. No resolver may return 0 for something it
  could not reach; unreachable and empty are opposite situations and the type system keeps them
  apart.
- *"Does this slow every call down?"* — The decision is a static comparison, measured at a 0.034ms
  median. The resolution is a provider round trip and that is the real cost; the repo does not have
  a live-tenant p50 for Graph yet and says so rather than modelling one.

**Do not** claim it prevents incidents. Two of the three incident anecdotes this project started
with turned out to be false on checking, and `eval/incidents.py` exists because of it. The credible
version is the mechanism and the numbers, which is what the post above leads with.

---

## Every number in the draft, and where it comes from

Checked against the repository rather than remembered, because a launch post is the worst place to
carry a figure nobody re-derived — and one draft of this already said `pip install 'neti[all]'`
where the README says, in as many words, *"No extras, no quoting."*

| claim | source |
|---|---|
| 41,203 principals, ceiling 200, and the exact denial sentence | `tests/golden/transcripts/hook_block.txt`, pinned byte for byte |
| five declared predicates | `SCOPE.md`, the table under "What `neti` does" |
| eleven resolvers | the landing page's own figure, and the coverage section |
| fifteen seams, byte-for-byte identical verdicts | `tests/e2e/test_seam_equivalence.py` |
| sixteen numbered gaps | `SCOPE.md` — sixteen `NC-` rows |
| ~80 lines to add a resolver | the landing page's contribution section |
| 0.034ms decision median | measured on a 2026 laptop; the budget and its headroom are in `tests/bench/test_decision_latency.py` |
| `pip install neti`, then `neti demo --here` | `README.md` §1 and §"Want the whole arc in one command?" |
| Apache-2.0, all of it | `LICENSING.md` |
| two of three incident anecdotes were false | `POSITION.md`, and `eval/incidents.py` exists because of it |

The one figure deliberately absent is a latency number for the resolution itself. The repo does not
have a live-tenant p50 for Microsoft Graph, and `tests/bench/test_decision_latency.py` says so at
the top rather than modelling one. Do not supply it in a comment.
