# The resolver contract

**This is the product.** The gate is commodity — OPA, Kyverno and `conftest` already express
`count(x) > N`, and Gatekeeper shipped an entire External Data protocol so admission policies could
call out mid-decision. What nobody ships is a resolver with a correctness specification. A policy
author who reaches for `http.send` writes a wrong one, because none of the questions below have an
obvious default.

## Protocol

```python
class Resolver(Protocol):
    unit: Unit
    def resolve(self, target: str, ctx: ResolveContext) -> Resolution: ...
    def reachable_max(self, ctx: ResolveContext) -> Resolution: ...
```

`resolve` answers "how big is *this* target". `reachable_max` answers "what is the largest thing this
resolver could ever return in this tenant" — it powers `neti inventory`, which is the hour-one
artifact that produces a finding with zero traffic and zero declared ceilings.

## The four rules

### 1. Direction is declared, never inferred

Every `RESOLVED` resolution carries `direction ∈ {exact, upper_bound, lower_bound}`. A sound ALLOW
needs `true ≤ measured`; a sound BLOCK needs `true ≥ measured`. So an **upper bound** can conclusively
allow but not conclusively block, and a **lower bound** can conclusively block but not conclusively
allow. A resolver that does not know its own direction is not finished.

Both unsound cases fail closed rather than being rejected: an upper-bound block stands and records
`over_block_possible`; a lower-bound that is under every threshold escalates to the declared
`on_unbounded` verdict instead of allowing.

BigQuery is the precedent for the first case. `maximum_bytes_billed` compares against an upper-bound
estimate and fails the query, and Google documents the caveat that "actual bytes scanned at execution
time can be lower because block pruning is applied during execution, not during query planning" —
i.e. it knowingly over-blocks. That is correct for a guard and would be a bug if the same estimate
were used to justify an allow.

### 2. `PARTIAL` is unmergeable

A truncated enumeration looks exactly like a small target, and the under-count direction is the
dangerous one. `PARTIAL` must never be reduced to a number, summed, cached, or defaulted. Any of the
following makes a resolution `PARTIAL`:

- a leftover continuation token / `nextPageToken` / `@odata.nextLink`
- a provider truncation flag (Drive's `incompleteSearch: true` is the canonical case)
- a page budget or byte cap hit mid-enumeration
- a partial failure inside a fan-out

Prefer providers where enumeration does not occur on the hot path. `transitiveMembers/$count` returns
an integer: **you cannot half-read an integer.**

### 3. No wall-clock in the decision

Timeouts, retries and backoff live entirely inside the resolver and surface as `UNRESOLVED`.
`neti.core.decide` never reads a clock, so a stored decision replays byte-identically. A resolver that
lets a deadline leak into the comparison has made the gate unreplayable.

### 4. Positive assertion — reject unless recognised

Never allow-unless-rejected. Two real silent-failure cases this rule exists for:

- Microsoft Graph **silently ignores** `?$count=true` when the `ConsistencyLevel: eventual` header is
  absent, and errors on `/$count` as a path segment. A naive client gets a plausible-looking response
  with no count and no error.
- `sqlglot` **silently returns** an opaque `exp.Command` node rather than raising when it cannot parse
  a dialect, so a "parsed" statement may be entirely unanalysed.

Every resolution therefore asserts, and fails closed if any assertion fails: the request carried the
headers it needed · the response status and `Content-Type` are the expected ones · the body parses to
the expected type · no continuation state remains · the value is within a sanity range.

## Fields, and why each exists

| field | why |
|---|---|
| `state` | three-valued; ignorance is representable |
| `unit` | one ceiling grammar across principals / apps / recipients / objects / bytes / rows |
| `magnitude` | `None` unless `RESOLVED`, so a missing count cannot be read as zero |
| `direction` | rule 1 |
| `resolved_at` | the auditable bound; provider clock where available |
| `consistency` | `strong` or `eventual` — a claim about the provider, stamped rather than assumed |
| `provider_snapshot` | index/etag/delta token: the replay key for the number |
| `breakdown` | sub-counts a ceiling can address separately, e.g. `{"internal": 3100, "guest": 412}` |
| `evidence` | request URL, status, resource-unit cost, page count — what an auditor reads |

## Adding a resolver

1. Answer, in the module docstring: what is the unit, what is the direction and why, what makes it
   `PARTIAL`, what makes it `UNRESOLVED`, and what is the cheapest exact-count endpoint.
2. If the provider has **no O(1) count**, say so in the docstring and expect the resolver to be
   observe-mode-only until a caching design exists. Enumerating a large target synchronously means the
   gate is slowest exactly when the action is most dangerous.
3. Add the target to the synthetic-fixture set with a known ground-truth magnitude.
4. Add the resolver's row to the failure-mode matrix (M3): 429, 5xx, timeout, expired credential,
   insufficient scope, missing required header, truncated enumeration, unsupported target type,
   deleted target.
