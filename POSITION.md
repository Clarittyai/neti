# Position

**Verified on 2026-08-10.** Every competitor claim below is a quote with the URL it was read from
and the date it was read. A competitive document that ages into being wrong is worse than none, so
this file carries the same discipline `eval/incidents.py` applies to incident anecdotes — where two
of the three stories this project started with turned out to be false.

`SCOPE.md` fixes what `neti` claims about itself. This file fixes what it claims about the market,
and the sentences we do not say about other people's products.

**A blockquote in this file is somebody else's words, always.** Nothing we wrote is set that way, so
every quotation can be required to carry its source and the date it was read — which is what
`tests/property/test_position_is_checkable.py` asserts, along with failing the build when this page
has gone a year without anybody re-reading the pages it quotes.

---

## 1. The category

### What this file used to say, and why it was wrong

The first version of this document argued that the axis was **measurement versus judgement**:
everyone else decides whether an action *looks bad*, we report *how big it is*, and a magnitude is
the primitive their engines are missing.

That argument does not survive one question: **does it matter how big?**

Sometimes. Size is the harm when the harm *is* the count — mass revocation, mass deletion, mass
send, an infrastructure destroy. It is not the harm for a prompt injection, a single wrong row, one
leaked credential, an over-permissioned agent, or shadow AI.

The honest number is in our own corpus. `neti score` M4 replays seven incidents: **three are caught
by magnitude**, and `eval/incidents.py` flags one of those three itself as *"the recognisable demo,
not the defensible claim"*, because Google Workspace and Purview already ship recipient-count
controls. So it is defensibly two of seven. A product whose entire thesis is magnitude is a narrow
product, and pretending otherwise in a security market is how you lose the room in one question.

### The actual axis

`neti` was never only a magnitude gate. `core/decide.py` joins **five declared predicates** and
takes the worst verdict any of them returns:

| predicate | the question | magnitude? |
|---|---|---|
| **magnitude** | how big is the set this argument addresses | yes |
| **sensitivity** | what *is* this target, whatever its size | no |
| **location** | is it outside the tree the agent was pointed at | no |
| **accumulation** | how much has this session touched already | no |
| **provenance** | is this session downstream of untrusted input | no |

Four of the five say nothing about size. `sensitive:` stops `.env` at a cardinality of 1. Provenance
is a prompt-injection control that never reads the prompt.

So the axis is not measurement versus judgement. It is:

### Declared, not learned.

**Every predicate here is written by a human, evaluated by a static comparison, sealed into a hash
chain, and re-derivable offline. Nothing in the decision path is scored, inferred, trained, or
served from a vendor.**

Magnitude stays the flagship, because it is the one nobody else computes. It is no longer the claim.

### Why *declared* is worth more than *accurate*

Every learned or heuristic control has a precision/recall dial. Tight enough to catch things means
blocking legitimate work; loose enough to stay out of the way means missing. There is no setting
where it is both — so it runs in detect mode, or it gets switched off in month two. That is how
security tools actually die, and everyone who has deployed one knows it.

A declared control has no dial. A false block is **always** *"the rule you wrote is too tight"* —
readable in five seconds, fixable in one line — and never *"the model found it suspicious."* That is
the property that lets enforce mode stay on, and `SCOPE.md` states it positively for exactly this
reason: no algorithmic false positives.

The second thing it buys: a decision that can be re-derived by the customer, months later, with
every vendor's servers unplugged. That is evidence. A rule engine's *"nothing fired"* is an absence,
and an absence is not evidence of anything.

---

## 2. Where we sit

```
                    authorization        may this identity do this at all?
                          │              Okta, Entra, CyberArk, Hush. Upstream of us.
                          ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  LEARNED / INFERRED            does this look bad?           │
   │  Zenity · Noma · Lasso · Lakera · Operant · Prisma AIRS       │
   │  rule libraries, taints, behaviour models, LLM assistants,    │
   │  evaluated on the vendor's infrastructure                     │
   └──────────────────────────────────────────────────────────────┘
   ┌──────────────────────────────────────────────────────────────┐
   │  DECLARED — neti               did this cross a line          │
   │                                somebody wrote down?           │
   │  five predicates · static comparison · sealed · replayable    │
   │  offline · no model in the decision path                      │
   └──────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                    containment          where can it run, what can it reach?
                                         sandboxes, network policy, scoped credentials.
                                         Downstream. See SCOPE.md NC-14, NC-15.
```

The two boxes in the middle are **side by side, not stacked**. They fail differently: a rule library
misses what nobody anticipated, and a declared gate misses what nobody declared. Neither subsumes
the other, and a serious organisation will want both.

What we do not want is to become the box above. It is contested by seven funded companies, there is
no ground truth to win on, and it is a capital game.

---

## 3. Head to head

### Zenity

The most complete platform in the category, and the one to read carefully.

> "the platform purpose-built to secure the decision"
> — [zenity.io/platform](https://zenity.io/platform), read 2026-08-10

Eight capabilities across three layers — *Surface* (AI Observability, AISPM, AI Exposure
Management), *Enforce* (Runtime Boundaries, AI IAM), *Protect* (AIDR, MCP Security, Guardian
Agents). Gartner named them the company to beat in AI agent governance (Apr 2026).

**Runtime Boundaries** is the capability that overlaps us. What it decides on, in their words:

> it considers "the agent, the human it's acting for, the action, the resource, live identity, and
> the taints attached to the session"
> — [zenity.io/platform/runtime-boundaries](https://zenity.io/platform/runtime-boundaries), read 2026-08-10

> a "plain-language AI policy assistant" that "turns a plain-language request into a working rule"
> — *ibid.*

> "Allow, modify, or block a tool call based on live context, not a static allow-list."
> — [zenity.io/platform/mcp-security](https://zenity.io/platform/mcp-security), read 2026-08-10

Note the last one is aimed squarely at us: *not a static allow-list* is a criticism of declared
policy, and it is a fair one as far as it goes. A static rule cannot adapt. The reply is not that
they are wrong — it is that a rule which cannot adapt is also a rule that cannot drift, cannot be
gamed by input, cannot change its mind between two runs, and can be re-checked by an auditor a year
later.

**Three differences that are structural, not a feature gap:**

| | Zenity | `neti` |
|---|---|---|
| **Re-derivability** | A verdict depends on a rule engine, live identity attributes, session history and an LLM assistant, on their infrastructure. A customer cannot reproduce it. | `neti verify --config` replays every verdict from its recorded evidence, offline, network unplugged, forever. |
| **Availability coupling** | The control plane sits in the decision path. Their uptime is your agent's uptime. | Ours can only ever make a decision **more** permissive, and only through a named human. Unreachable, absent or unpaid behaves exactly as the free tier. Asserted by `tests/property/test_licence_boundary.py`. |
| **Stated non-coverage** | None published. | NC-01 … NC-16, numbered so tests and write-ups can cite them, printed by `neti score` rather than filed in an appendix. |

**Where they are better, stated plainly.** Their rule library works on day one; ours is an empty
YAML and a week of observation before you have a number you would defend. `neti propose` shortens
that and does not remove it. And for the small, precise, novel harm — one credential read, one admin
revoked — a curated rule library plausibly catches things our declarations have not been written for.
`SCOPE.md` NC-02 and NC-05 are exactly that admission.

### The rest of the field

Read 2026-08-10 from public marketing and third-party comparisons. None were evaluated hands-on;
this says what they position on, not how well they do it.

| vendor | positions on | how it relates |
|---|---|---|
| **Noma Security** | Agentic Risk Map, AI-SPM, 80+ integrations | Posture and inventory. Ranks; does not count. Adjacent, not overlapping. |
| **Lasso Security** | Behavioural intent — model what an agent is trying to accomplish, flag deviations | The furthest from declared of anyone here, and the most exposed to drift. |
| **Lakera · Operant** | Runtime guardrails in the call path, sub-50ms, injection and exfil | Closest architecturally. Content classification, not target rules. Genuinely complementary. |
| **Prompt Security · WitnessAI** | Shadow-AI discovery, usage governance | Which agents exist and who uses them. Orthogonal — and a real gap of ours. |
| **Prisma AIRS · CrowdStrike · Wiz** | Platform consolidation | They will acquire before they build. |
| **OPA · Cedar · conftest** | The policy languages an enterprise already runs | **Not competitors — the closest thing to a peer.** Declared, static, replayable, no model. `conftest`'s `max_auto_apply_changes` is magnitude gating done right in one domain. The first integration target. |

### Hush Security — the clearest complement in the field

Not a competitor at all, and worth its own section because it is the first vendor read here whose
product is the box `SCOPE.md` NC-04 already points at.

> "give every agent its own identity and delegated permissions. Every action centrally governed,
> scoped to the task, and revoked when done"
> — [hush.security](https://www.hush.security/), read 2026-08-10

> brokers "short-lived credentials at runtime, scoped to the action and gone when the task ends"
> — *ibid.*

That is non-human identity, least privilege, and just-in-time credential brokering — *authorization*,
which NC-04 says out loud is a different question answered by a different layer, and which `neti`
runs after.

**They compose, and the composition is genuinely better than either half.** Least privilege bounds
what an agent *can* reach. It does not bound *how much of it at once*. An agent correctly scoped —
by Hush, by Entra, by anything — to the engineering directory can still, in one authorized call,
remove all 41,203 people in it. Scope is a boundary; magnitude is the size of a single action inside
that boundary, and no amount of scoping produces it.

The reverse is at least as true, and our own corpus says so. `pocketos-railway` is published as a
**miss** with the note that *"the proximate cause was an unscoped credential, which is an
authorization problem upstream of a magnitude gate (NC-04)."* That incident is Hush's category, not
ours. A miss table that already contains someone else's win is the most credible possible basis for
saying the two layers belong together.

Where we overlap slightly and should be honest: their discovery of agents and MCP servers is
inventory, which §4 concedes entirely.

---

## 4. What we concede

Written here, in our own document, before anyone writes it for us.

- **Magnitude alone is narrow.** Two to three of our own seven corpus incidents. Section 1 rather
  than a footnote.
- **Inventory and discovery.** They build a live inventory of agents across SaaS, custom and
  endpoint deployments. We build one policy file on one machine.
- **Posture management.** Pre-deployment assessment of agent configuration, permissions and
  integrations. We have none of it.
- **Exposure paths.** Correlating identity, data and behaviour into validated exploitable attack
  paths is genuinely useful and genuinely not what a gate does.
- **Enterprise-SaaS agent coverage.** Copilot Studio, Agentforce, ServiceNow, Bedrock, Vertex,
  ChatGPT Enterprise, Claude Enterprise. Our coverage of hosted runtimes that execute tools
  server-side is **zero**, for a structural reason `neti score` already prints: there is no local
  seam to sit at.
- **The SOC workflow.** Triage, investigation, case management, response automation. We produce a
  record chain and an exit code.
- **Time to value.** A declared gate needs declarations. That is a slower sale than a rule library.
- **Coverage of the MCP ecosystem, today.** `neti init` gates **25 of 160** discovered tools across
  the 13 of 22 catalogued servers that launch — 15.6%. The corpus holds **401 parameters no rule
  claims**. This is the honest bottleneck of the entire product and it is on the scorecard.

What we do not concede is that a rule match is a sufficient account of what an action will do, or
that a verdict a customer cannot reproduce is evidence.

---

## 5. The moat, corrected

**The previous version of this file claimed resolvers as the moat. That was weak.** Eleven
resolvers is roughly nine hundred lines. A funded team ships thirty in a quarter if they decide
magnitude matters — so "they haven't bothered" is not a moat, it is a bet on their roadmap.

Three things are actually hard here, and none of them is a resolver.

**The seam surface.** Fifteen doors a tool call can arrive through — MCP stdio and HTTP, the Claude
Code `PreToolUse` hook, and eleven SDK adapters — every one asserted to produce the same verdict,
the same magnitude and the same denial sentence *byte for byte*. That is not integration work, it is
a correctness property, and each seam is subtle in its own way: CrewAI's documented blocking hook is
the wrong seam because it substitutes a fixed string for the reason, so the number never reaches the
model. You only find that by driving a real `Crew.kickoff()`, which is why `tests/conformance/`
drives an actual agent loop in eleven frameworks with the model scripted and no key required.

**The evidence chain.** Sealed as decisions are made, re-derived offline, and replayable *against
the policy* so "the chain is unbroken" becomes "and every verdict in it still follows from its
evidence." Nothing built on a learned predicate can offer this, because a learned predicate cannot
be re-run to the same answer.

**The discipline, which is the one that compounds.** Two rules from `RESOLVER_CONTRACT.md`: never
return `0` for something you could not reach, and anything capped or estimated reports
`LOWER_BOUND` — sound to block on, never sound to allow on. Plus the habit of publishing the misses:
NC-01…NC-16, the incident corpus, the M10 coverage number, and the measurement that
`neti suggest` on a local 8B model was roughly six percent right. A competitor can copy a resolver
in an afternoon. Copying the reason a security evaluator believes the rest of the page takes years,
and most vendors have already spent that credibility.

---

## 6. Things we do not say

Extending `SCOPE.md`'s list of sentences this project refuses, to claims about the market.

- ❌ "Zenity can't block agents."
  → ✅ "Zenity blocks on a rule match. It does not report a magnitude, a unit, or a soundness
  direction, and its verdict is not re-derivable by the customer."

- ❌ "Magnitude is what matters."
  → ✅ "Magnitude is the predicate nobody else has, and it catches two to three of the seven
  incidents in our own corpus. Four other declared predicates carry the rest."

- ❌ "We're the only runtime gate for AI agents."
  → ✅ "We are the only gate that resolves a symbolic argument to the cardinality of the set it
  affects. Several products enforce at runtime; none of them count."

- ❌ "Nobody gates on magnitude." — retired in `SCOPE.md`, and it stays retired.
  → ✅ "Google Workspace and (reportedly) Purview gate on recipient count; MySQL, BigQuery and
  `conftest` gate on rows, bytes and plan size. Nobody resolves a symbolic identity target to the
  principals and applications that lose access."

- ❌ "More secure than Zenity."
  → ✅ "It fails differently. A rule library misses what nobody anticipated; a declared gate misses
  what nobody declared. On the questions their stack asks, they have more than we do — §4."

- ❌ "Static policy is a limitation." (their framing) or "Adaptive policy is a liability." (the
  mirror image)
  → ✅ "A rule that cannot adapt is a rule that cannot drift, cannot be gamed by input, cannot
  change its mind between two runs, and can be re-checked a year later."

- ❌ Any comparison sourced from a competitor-comparison blog post. The market is full of vendor
  content marketed as analysis. Only a vendor's own pages, quoted and dated, appear above.

---

## 7. What this position commits us to

Not a feature list — the four gaps **we ourselves publish as misses**. Closing your own published
misses is the only roadmap consistent with §5.

| | gap | why it is the right next thing |
|---|---|---|
| **G1** | **Accumulation** — NC-01 / NC-12. Budgets that survive a restart and span a window and a fleet. | Two of the seven corpus misses are this exact shape: many calls of magnitude 1. It is the real exfiltration shape, and it is caught by counting rather than by pattern-matching a behaviour model. |
| **G2** | **Provenance** — deepen the taint axis: persistence across restarts, more declared sources. | The strongest non-magnitude idea in the codebase and the closest to what the market actually fears. It answers a mechanical question an attacker cannot write over: *did this session already touch something declared untrusted?* No model, no prompt reading. |
| **G3** | **Consequence, not cardinality** — NC-02 / NC-05. Irreversibility as a declared property beside `sensitive:`. | The admission in §4 that a curated rule library beats us on the small precise harm. This is the declared answer to it. |
| **G4** | **The shell and generated code** — NC-14 / NC-15. **Narrowed, never closed.** | The biggest honest hole for our strongest audience. A wrapper hides the verb entirely and no textual signal will ever see it. What bounds it is the sandbox, and we say so rather than claim otherwise. |

### What this position forbids

- **No model in the decision path.** Extended from one predicate to five. It is the property the
  whole page rests on.
- No behavioural anomaly detection, no learned thresholds, no scoring. Declared or absent.
- No inventory / posture / exposure-path platform. Conceded in §4 and it stays conceded.
- No claim that G4 closes NC-14 or NC-15.
