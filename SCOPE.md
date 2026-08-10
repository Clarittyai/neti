# Scope

Frozen before implementation. Changing this file changes what `neti` claims, so changes are
deliberate and dated.

## What `neti` does

`neti` decides a proposed tool call before it executes, by joining a small set of **declared**
predicates and taking the worst verdict any of them returns.

**This section said "It answers *how big*, and nothing else" until 2026-08-10.** That sentence was
written when magnitude was the only predicate, and it stayed after four more shipped — so the file
undersold the product in one direction and misdescribed it in another. `sensitive:` stops `.env` at
a cardinality of 1, which is not a statement about size at all. What follows is what `decide()`
actually joins.

| predicate | the question it answers | declared as |
|---|---|---|
| **magnitude** | how big is the set this argument addresses | `tools.<tool>.gate.<param>.bands` |
| **sensitivity** | what *is* this target, whatever its size | `sensitive:` |
| **location** | is this target outside the tree the agent was pointed at | `outside_root:` |
| **accumulation** | how much has this session touched already | `session_budgets:` |
| **provenance** | is this session downstream of untrusted input | `provenance:` |

Magnitude is the one nobody else has, and it remains the flagship. It is not the whole claim, and
reading this file as though it were is how NC-02 and NC-05 came to be read as harsher than they are:
both are *partially* addressed, on the two axes named in their rows, and both say so.

The properties that hold across all five. Its resolution is **eventually consistent**, so there is a
window in which a verdict is provably wrong; every decision carries `resolved_at` and a consistency
class instead of a freshness claim. Its coverage is exactly what the operator declared, times the
resolvers that exist. Where it cannot resolve, it does not guess: `UNRESOLVED` and `PARTIAL` are
first-class states routed to a declared fail-closed policy. Verdicts combine by JOIN, so a predicate
can only ever make a decision stricter — adding one cannot quietly allow something the others
stopped.

And what it still does **not** do. It does not know whether the action is *correct* — a single-row
delete of the one row that mattered is invisible unless a `sensitive:` rule happens to name it. It
does not establish authorization; that is upstream. It does not contain damage or undo it.

## One property stated positively

The gate has no algorithmic false positives. A false block is always a mis-declared rule — a ceiling
that is too low, a glob that is too wide — never a mis-scored value. There is no threshold to tune,
**no model to drift in the decision path**, and nothing to calibrate on a corpus.

This holds for all five predicates, not just magnitude, and it is the property the whole product
rests on: every one of them is a static comparison against something a human wrote, sealed into a
chain, and re-derivable offline. **Declared, not learned.** That is what makes a false block
something an operator can read in five seconds and fix, which is in turn what makes enforce mode
survivable on a real agent for longer than a month.

The qualifier arrived with `neti suggest` (2026-08-03) and is not a hedge. That command calls a
model, so the sentence had to get more precise or become untrue. What it does is ask which
unclaimed parameters name a set; what it produces is a commented-out YAML fragment in a file the
gate never loads. Nothing a model says can reach a verdict without a person deleting a `#` and
committing the result, and even then the bands are empty, so the gate is still a static integer
comparison against numbers a human wrote.

**The contamination path that does exist, stated rather than hidden.** A wrong suggestion, once
uncommented and merged, records magnitudes measured by the wrong resolver; `neti report` shows
them and `neti propose` could derive a ceiling from them. That is two deliberate human steps
deep, and a wrong resolver almost always reports UNRESOLVED rather than a plausible number, which
surfaces in the first week. It is still a path, and it is written down here rather than left for
somebody to find.

**How often a suggestion is wrong, measured rather than assumed** (2026-08-05). M12 arm C turns a
model loose on the 401 parameters no rule claims and scores it against a written adjudication
(`eval/answers/adjudicate.py`, an opinion, with every label carrying the rule that produced it). A
local 8B model found 7 gates the rule table genuinely misses — every one that the adjudication says
exists — and paid for them with 92 claims on parameters that are not sets and 7 sets forced into a
resolver that cannot size them. Roughly six percent of its claims were right.

That is the number to read the paragraph above against. A suggestion is not a coin flip that
occasionally misfires; on a small model it is wrong far more often than it is right, and the only
reason that is tolerable is that every claim arrives commented out, with empty bands, in a file the
gate never loads. Run it against a larger model and the rate will differ — the harness prints it,
`neti score` carries it, and neither reports a number nobody measured on this machine.

## Non-coverage

Numbered so the scorecard, the tests and any external write-up can cite them. `neti score` prints
this list as part of its output, not as an appendix.

| id | Not covered | Why it is structural, not a gap to close |
|---|---|---|
| **NC-01** | **Cumulative effect across calls.** 4,000 individual sends are 4,000 calls of one recipient each; per-call resolution sees `1` every time. | Mitigated only by *declared budgets*, never by resolution. Without a declared budget for the tool, this is invisible. **This claim was false where it mattered most until now**: `SessionTally` lived in memory on the `Engine`, and `neti hook` is one process per tool call — so a declared budget could never fire on the integration the product is mostly installed through. Totals persist beside the records now, and `neti start` declares one. **A budget was also only ever per-conversation until 2026-08-10**, which mitigated one run going wrong and nothing slower: a new session started the total at zero, so an agent reading steadily for three days tripped nothing. `window:` now takes `session`, `day`, `week` or `rolling:<n>h`, and a calendar window resets on its boundary — a `day` budget of 20,000 permits 40,000 across one midnight, which is why `rolling:` exists and why both are declared rather than inferred. **Every one of those totals is still per machine**, so a fleet of forty agents has forty separate daily budgets; pooling them needs shared state, which is the paid tier (`LICENSING.md`). The client for it is Apache-2.0 and in this repository, and it falls back to the local total when the control plane is unreachable — an outage under-counts rather than over-blocks, so a fleet budget silently becomes a per-machine one for the duration. |
| **NC-02** | **Correctness of the action.** Deleting the one row that mattered. | Magnitude is the wrong primitive. A cardinality of 1 is always under every ceiling. *Partially* addressed on two axes, both declared. **Provenance**: a call downstream of untrusted input is judged against a tighter ceiling whatever its size — **and this was inert through `neti hook` until 2026-08-10**, exactly as the NC-01 mitigation was: the taint lived in a dict on the `Engine` and the hook is one process per tool call, so every call started from a clean session. Worse than the budget version of the same defect, because a taint *latches* — losing it does not under-count, it switches the axis off. Demonstrated before the fix: an untrusted read followed by a five-file glob against a tainted band of 2 was ALLOWED through the hook and BLOCKED in the gateway. Taints persist beside the tallies now. **Sensitivity**: a target matching a declared rule is gated on what it is rather than how much of it there is, so `.env` can be stopped at cardinality 1 — and since 2026-08-10 a rule can also name the *tools* it applies to, so reading `.env` and overwriting it are two different verdicts about one object. Neither reaches an *undeclared* small target in a clean session, and nothing built on counting ever will. |
| **NC-03** | **Which tool was called, in what order, or what was omitted.** | `neti` sees a proposed call, not a plan. Tool-level authorization is upstream. |
| **NC-04** | **Whether the caller should be doing this at all.** | Authorization is a different question, answered by a different layer. `neti` runs after it. |
| **NC-05** | **Low-cardinality but high-consequence targets.** Revoking one admin's access. | Same as NC-02: consequence is not cardinality — which is why the answer is a second comparison rather than a better number. A `sensitive:` rule matching the target fires whatever its size. **And since 2026-08-10 a rule need not name a target at all**: `{ tools: [delete_repository], verdict: block }` fires on the *act*, with no glob, no resolver and no magnitude — which is what "revoking one admin's access" actually needs, because the dangerous thing there is the verb rather than the operand. Before it, requiring a human for an irreversible operation meant inventing a resolver binding for it, and where none fitted there was no way to say it at all. This is a narrow, declared exception to NC-09: an ungated tool is out of scope means a tool *nobody mentioned*, and one named in `sensitive:` has been mentioned. It still only covers what somebody wrote down: the value moved from *impossible* to *declared*, not to *inferred*. |
| **NC-06** | **Exchange dynamic distribution groups.** | Not synced to Entra; invisible to Graph at any endpoint. Resolves `UNRESOLVED`, never `0`. |
| **NC-07** | **Entitlements inside downstream apps.** "23 people lose access to 7 applications" is resolvable; "23 people lose the ability to approve invoices" is not. | No IdP exposes the in-app entitlement graph. One hop only. |
| **NC-08** | **Staleness window.** Graph's `$count` is served from a secondary index that cannot be forced current. | Provider limitation. `neti` sells an auditable bound, not freshness. |
| **NC-09** | **Ungated tools and undeclared parameters.** | Coverage is the operator's declaration. `unknown_tool: allow` is deliberate: an ungated tool is out of scope, not denied. |
| **NC-10** | **Exact row counts, and any statement `db.rows` does not certainly recognise.** | `db.rows` counts with `select count(*)`, not `EXPLAIN`, so the low bias of planner estimates never applies. `ON DELETE CASCADE` fan-out is still invisible, so every result is a `LOWER_BOUND`: sound to block on, never sound to allow on, and a three-row delete does not get to claim it is small. It recognises `DELETE FROM t [WHERE p]` and `UPDATE t SET … [WHERE p]` only; multi-table forms, comments, multiple statements and anything ambiguous resolve UNRESOLVED. Reading a statement is still a *syntactic* gate and a weaker claim than reading a value, which is why it declines far more than it accepts. |
| **NC-11** | **Containment and rollback.** | Different products. `neti` decides before; it does not clean up after. |
| **NC-12** | **Reads that are individually small but collectively large.** | Same shape as NC-01, and the reason the Glean-8M-files case needs a budget on `objects` rather than a per-call ceiling. It also needs the right *window*: that volume accumulated across many retrievals, so a `session` budget could miss it entirely and `day`, `week` or `rolling:` is the declaration that sees it. |
| **NC-14** | **What a code-executing agent's generated code does directly.** smolagents' `CodeAgent`, and anything shaped like it, does not emit tool calls — it writes Python and runs it, and calling a tool is only one of the things that Python may do. | The adapter gates every call that goes *through* a tool, which is what a tool-boundary gate can see. `open(...)`, `os.remove(...)`, a `subprocess`, an import that reaches the network: none of those cross a tool boundary, so no gate at one can size them. The thing that bounds them is the executor the code runs in — its sandbox and its import allow-list. Stated here because a reader who assumes otherwise has assumed something dangerous. |
| **NC-15** | **A shell command whose destruction is not visible in the string.** `./cleanup.sh` deletes; so does a Makefile target, a `python -c` one-liner, and code an agent generated a second ago. | `shell.paths` is textual. It sizes a small explicit set of forms (`rm`, `find -delete`, `git clean -fd`, `git checkout -- .`) and declines everything else, and `on_unsized_risk` makes the *recognised-but-unsizeable* half visible — `cat list.txt \| xargs rm` is a flagged, recorded call rather than an indistinguishable pass. **The flagging half was widened on 2026-08-10** to the verbs an agent reaches for that leave no `rm` behind: `sed -i`, `tee` without `-a`, `rsync --delete`, `git branch -D`, `git stash drop`/`clear`, `docker rm`/`rmi`/`prune`, `kubectl delete`, `terraform destroy`, `aws s3 rm`/`rb`, `gsutil rm`. Only the *flag* half — none of them teaches sizing a number, because flagging costs a line in a report and mis-sizing lets a deletion through under a ceiling. Every one ships with the non-destructive spelling of itself as a negative test (`sed` without `-i`, `tee -a`, `git branch -d`), because a recogniser that fires on both teaches an operator to stop reading the flag. `chmod -R` is deliberately absent: it can make a tree unusable and destroys no data, and folding that into the same signal as deletion makes the signal mean less. It does not close the gap. A wrapper hides the verb entirely and no textual signal will ever see it, so the flag narrows what escapes silently, it does not make the shell covered. What bounds a wrapper is the same thing that bounds NC-14: the sandbox the command runs in. **`neti score` prints this as M14** rather than leaving it here to be found — including whether the policy in front of you gates `Bash` at all, and a refusal to print a count of recognised forms: that number would read as coverage, and there is no denominator, which is the entire content of this row. |
| **NC-16** | **An agent that can write to the gate's own files.** The policy, the hook wiring in `.claude/settings.json`, and the record chain all sit in the tree the agent works in. | Day zero declares all three off limits, so `Write(neti.yaml)`, `rm -rf .claude` and `truncate out/decisions.ndjson` are `confirm` rather than silent. That is a speed bump on the honest path, not a boundary: the rules live *in* the file they protect, so an operator who removes them removes the protection, and anything that writes without crossing a tool boundary — NC-14, NC-15 — is unaffected. **A gate that lives inside the blast radius cannot fully protect itself.** What bounds this properly is filesystem permissions or a policy the agent's user cannot write, and neither is something `neti` can arrange for you. Measured against 0.3.2, before the rules existed: all eight spellings were allowed in silence. |
| **NC-13** | **A record chain with a gap in it.** A full disk, a permissions change or a records path that is not writable means a decision is made, enforced, and not filed. | Deliberate, and the alternative is worse. Recording is evidence; the verdict does not depend on it, and a log file that cannot be opened must not be able to switch enforcement off — which is precisely what it used to do. So the call is still gated, the operator is told on stderr, and `neti verify` reports the break. What `neti` will not do is pretend the record is there, and it will not stop deciding because it is not. |

## Things we do not say

- ❌ "Prevents agents from doing damage." → ✅ "Blocks calls whose resolved magnitude exceeds a
  ceiling you declared."
- ❌ "Nobody gates on magnitude." → ✅ "Google Workspace and (reportedly) Purview gate on recipient
  count; MySQL, BigQuery and `conftest` gate on rows, bytes and plan size. Nobody resolves a symbolic
  identity target to the principals and applications that lose access."
- ❌ "Zero false positives." → ✅ "No algorithmic false positives; a false block is a mis-declared
  ceiling."
- ❌ "Uses AI to find what to gate." → ✅ "`neti suggest` asks *your* model, with *your* key, which
  unclaimed parameters name a set. The answer is a commented-out block a person uncomments.
  Nothing a model said reaches a decision, and neti never proxies the request."
- ❌ "Learns what normal looks like." → ✅ "`neti propose` shows you your own distribution so you can
  declare a number. The number is static config; nothing learned reaches the decision path."
- ❌ Any claim about a specific public incident that needs a resolver we have not shipped. The
  scorecard's incident-replay table reports the misses.
