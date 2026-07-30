# Scope

Frozen before implementation. Changing this file changes what `neti` claims, so changes are
deliberate and dated.

## What `neti` does

`neti` resolves a symbolic argument in a proposed tool call to the cardinality of the set it will
affect, compares that cardinality to a ceiling the operator declared, and returns a verdict before
the call executes. It answers *how big*, and nothing else.

It does **not** know whether the action is correct — a single-row delete of the one row that mattered
is invisible to it, because magnitude is the wrong primitive for that harm. It does **not** see
cumulative effect unless a per-session budget is declared. It does **not** establish authorization —
that is upstream. It does **not** contain damage or undo it. Its resolution is **eventually
consistent**, so there is a window in which a verdict is provably wrong; every decision carries
`resolved_at` and a consistency class instead of a freshness claim. Its coverage is exactly the tools
and parameters the operator declared, times the resolvers that exist. Where it cannot resolve, it does
not guess: `UNRESOLVED` and `PARTIAL` are first-class states routed to a declared fail-closed policy.

## One property stated positively

The gate has no algorithmic false positives. A false block is always a mis-declared ceiling, never a
mis-scored value. There is no threshold to tune, no model to drift, and nothing to calibrate on a
corpus.

## Non-coverage

Numbered so the scorecard, the tests and any external write-up can cite them. `neti score` prints
this list as part of its output, not as an appendix.

| id | Not covered | Why it is structural, not a gap to close |
|---|---|---|
| **NC-01** | **Cumulative effect across calls.** 4,000 individual sends are 4,000 calls of one recipient each; per-call resolution sees `1` every time. | Mitigated only by *declared session budgets*, never by resolution. Without a declared budget for the tool, this is invisible. |
| **NC-02** | **Correctness of the action.** Deleting the one row that mattered. | Magnitude is the wrong primitive. A cardinality of 1 is always under every ceiling. |
| **NC-03** | **Which tool was called, in what order, or what was omitted.** | `neti` sees a proposed call, not a plan. Tool-level authorization is upstream. |
| **NC-04** | **Whether the caller should be doing this at all.** | Authorization is a different question, answered by a different layer. `neti` runs after it. |
| **NC-05** | **Low-cardinality but high-consequence targets.** Revoking one admin's access. | Same as NC-02: consequence is not cardinality. |
| **NC-06** | **Exchange dynamic distribution groups.** | Not synced to Entra; invisible to Graph at any endpoint. Resolves `UNRESOLVED`, never `0`. |
| **NC-07** | **Entitlements inside downstream apps.** "23 people lose access to 7 applications" is resolvable; "23 people lose the ability to approve invoices" is not. | No IdP exposes the in-app entitlement graph. One hop only. |
| **NC-08** | **Staleness window.** Graph's `$count` is served from a secondary index that cannot be forced current. | Provider limitation. `neti` sells an auditable bound, not freshness. |
| **NC-09** | **Ungated tools and undeclared parameters.** | Coverage is the operator's declaration. `unknown_tool: allow` is deliberate: an ungated tool is out of scope, not denied. |
| **NC-10** | **Row-count gating on SQL predicates.** | `EXPLAIN` estimates are biased *low* — the dangerous direction — and `ON DELETE CASCADE` fan-out is invisible to the plan. Any predicate support is a *syntactic* gate and a weaker claim. |
| **NC-11** | **Containment and rollback.** | Different products. `neti` decides before; it does not clean up after. |
| **NC-12** | **Reads that are individually small but collectively large.** | Same shape as NC-01, and the reason the Glean-8M-files case needs a session budget on `objects` rather than a per-call ceiling. |

## Things we do not say

- ❌ "Prevents agents from doing damage." → ✅ "Blocks calls whose resolved magnitude exceeds a
  ceiling you declared."
- ❌ "Nobody gates on magnitude." → ✅ "Google Workspace and (reportedly) Purview gate on recipient
  count; MySQL, BigQuery and `conftest` gate on rows, bytes and plan size. Nobody resolves a symbolic
  identity target to the principals and applications that lose access."
- ❌ "Zero false positives." → ✅ "No algorithmic false positives; a false block is a mis-declared
  ceiling."
- ❌ "Learns what normal looks like." → ✅ "`neti propose` shows you your own distribution so you can
  declare a number. The number is static config; nothing learned reaches the decision path."
- ❌ Any claim about a specific public incident that needs a resolver we have not shipped. The
  scorecard's incident-replay table reports the misses.
