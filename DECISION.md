# The decision procedure

One page. Frozen before implementation. `src/neti/core/decide.py` is this document in code and must
not diverge.

## Lattices

**Verdict**, ordered by severity, combined by **join** (most-restrictive-wins):

```
ALLOW  <  FLAG  <  CONFIRM  <  BLOCK
```

*Not claimed as IP* — Faramesh has PERMIT/DEFER/DENY and Slack confirms at N. It is UX.

**Resolution state**, ordered by how much we know:

```
RESOLVED   exact cardinality obtained
PARTIAL    enumeration truncated — UNMERGEABLE, never becomes a number
UNRESOLVED timeout / 429 / 5xx / unsupported target
```

**Direction** of a resolved magnitude, always declared by the resolver, never inferred. A sound ALLOW
needs `true ≤ measured`; a sound BLOCK needs `true ≥ measured`:

| direction | sound ALLOW | sound BLOCK | behaviour in the unsound case |
|---|:--:|:--:|---|
| `exact` | ✅ | ✅ | — |
| `upper_bound` | ✅ | ❌ | block stands (safety first) and records `over_block_possible` |
| `lower_bound` | ❌ | ✅ | cannot allow; escalates to the declared `on_unbounded` verdict |

Both unsound cases therefore fail closed. Mixing directions silently is how a conservative estimate
becomes either a mystery block or, far worse, an unjustified allow.

## Procedure

```python
def decide_arg(ptr, ceiling, res) -> ArgDecision:
    if res.state is not RESOLVED:
        return ArgDecision(ptr, ceiling.on_unresolved, res)  # declared per param
    for band in ceiling.bands:  # descending by `above`
        if res.magnitude > band.above:
            # over a threshold: sound iff true >= measured
            return ArgDecision(
                ptr, band.verdict, res, over_block_possible=not may_block(res.direction)
            )
    # under every threshold: sound iff true <= measured
    if not may_allow(res.direction):
        return ArgDecision(ptr, ceiling.on_unbounded, res)  # declared, default CONFIRM
    return ArgDecision(ptr, ALLOW, res)


def decide(call, policy, session) -> Decision:
    if not policy.is_gated(call.tool):
        return Decision(ALLOW, [], None)  # NC-09: out of scope, not denied
    per_arg = [decide_arg(p, c, resolutions[p]) for p, c in policy.gated_args(call)]
    budget = check_session_budgets(call, per_arg, session, policy)  # NC-01
    verdict = join(*[d.verdict for d in per_arg], budget.verdict)
    return Decision(verdict, per_arg, budget)
```

`decide()` is pure. It takes resolutions as input; it never performs I/O and never reads a clock.
Timeouts live in the resolver and surface as `UNRESOLVED`. This is what makes a decision replayable.

## Mode

- `observe` — the verdict is computed and recorded; the call is **always forwarded**. This is the
  default, and it is what makes installing `neti` a reversible config change rather than a risk.
- `enforce` — `ALLOW`/`FLAG` forward; `CONFIRM` and `BLOCK` return a structured tool error instead of
  calling upstream. Per-tool opt-in; there is no all-or-nothing switch.

## The denial

Structured and informative, unlike a provenance gate — there is no oracle to leak, and an agent
re-planning to a smaller scope is the desired outcome:

```json
{"error": "preflight_ceiling_exceeded", "unit": "principals",
 "resolved": 412, "ceiling": 200, "decision_id": "..."}
```

## Invariants (each is a property test)

1. **Monotone.** Raising a resolved magnitude never lowers verdict severity.
2. **No silent allow on ignorance.** `UNRESOLVED` and `PARTIAL` never yield `ALLOW` unless the
   operator declared `on_unresolved: allow`.
3. **Direction respected.** A `lower_bound` resolution can never produce `ALLOW`. An `upper_bound`
   resolution that produces `BLOCK` always carries `over_block_possible`.
4. **Pure.** `neti.core` imports nothing that performs I/O, reads a clock, or reads the environment;
   asserted at import time in the test suite.
5. **Deterministic.** Given `(resolutions, policy)`, the canonical record is byte-identical across
   processes and across `PYTHONHASHSEED` values.
6. **Sensitive.** Changing any input magnitude, ceiling or direction changes the canonical record.
   Without this, invariant 5 also passes for a constant function.
