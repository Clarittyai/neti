# Licensing

Two licences, one repository, and a boundary you can check with a test rather than take on trust.

| Path | Licence | What it is |
|---|---|---|
| `src/neti/**` | [Apache-2.0](LICENSE) | The gate. Everything that runs on your machine. |
| `src/neti_cloud/**` | [BUSL-1.1](LICENSE-cloud) → Apache-2.0 on 2030-07-31 | The control plane. Everything that needs a server. |
| `web/**` | Apache-2.0 | The console. Its Team screens talk to the control plane; they are useless without one, and free without it. |
| `tests/**`, `examples/**`, docs | Apache-2.0 | |

## Where the line is

**Can one machine do this?** If yes, it is Apache-2.0 and free forever.

That is not a slogan, it is the actual rule, and it decides every case. Enforcement runs on one
machine, so blocking is free. A second person approving a call does not run on one machine, so it is
not.

### Free, Apache-2.0 — the whole gate

The engine and the decision procedure. All three integration seams: MCP over stdio and HTTP, the
Claude Code `PreToolUse` hook, and the in-process `Preflight`. Observe mode **and enforce mode**.
`neti init`, `inventory`, `report`, `propose`, `verify`, `score`. The record chain and its
verification. The local console, all seven screens.

A single developer, or a small team sharing a policy file in git, never needs anything else. Nothing
is withheld, nothing is time-limited, and there is no telemetry.

### Paid, BUSL-1.1 — the things one machine cannot do

- **Approvals.** `CONFIRM` means *a human other than the agent's operator must decide*. Asking that
  human requires somewhere for the request to go and somewhere for the answer to come back.
- **Organisation policy.** One version, signed, pinned by digest, across every agent. Without a
  server each machine has its own file, and they drift.
- **Session budgets that survive a restart.** Today's tallies live in memory in one process
  (`Engine._tallies`), so a restart mid-session resets the cumulative total to zero — the mitigation
  for `SCOPE.md` NC-01 quietly stops applying. A shared tally needs shared state.
- **Audit and fleet across every agent.** Per-machine chains, anchored in one place, so a deleted
  local file becomes detectable.

We are not inventing limitations to sell past. Every item above is a hole `SCOPE.md` already
documents.

## Three properties this split has to keep

1. **The control plane can only ever make a decision *more* permissive, and only through a named
   human.** If it is unreachable, absent, or unpaid, the gate behaves exactly as the free tier —
   `on_approval_unavailable` defaults to `block`, which is precisely what free does with a `CONFIRM`.
   Enforcement takes on no new availability risk by paying us.

2. **There are no licence checks in the Apache-2.0 code.** No key validation, no kill switch, no
   phone-home. The entitlement is *possession of a control plane* — the client for it is open source
   and does nothing without a server to talk to. `tests/property/test_licence_boundary.py` asserts
   that `neti` never imports `neti_cloud`, the same way
   `tests/property/test_core_is_pure.py` asserts `neti.core` reaches nothing outward.

3. **Your records stay yours and stay verifiable offline.** The local NDJSON is the source of truth.
   The control plane anchors it; it is not an authority over it. `neti verify` works with the network
   unplugged, forever.

## Commercial use

The Additional Use Grant permits unlimited internal use by one organisation. What it does not permit
is offering the control plane to third parties as a hosted service. If you want that, or a licence
without the BUSL terms, get in touch.
