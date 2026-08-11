# Licensing

**This repository is Apache-2.0. All of it.** There is no second licence in here, no directory you
have to check, and no clause that expires.

| Path | Licence | What it is |
|---|---|---|
| `src/neti/**` | [Apache-2.0](LICENSE) | The gate. Everything that runs on your machine. |
| `web/**` | Apache-2.0 | The console. Its Team screens talk to a control plane; they are useless without one, and free without it. |
| `tests/**`, `eval/**`, `examples/**`, docs | Apache-2.0 | |

The control plane — the paid tier — used to live in this repository under BUSL-1.1, in `cloud/`. It
now has [its own repository](https://github.com/Neti-Security/neti-cloud). That was not a way of hiding
anything, and the section below is the reason it is not.

## Where the line is

**Can one machine do this?** If yes, it is Apache-2.0 and free forever.

That is not a slogan, it is the actual rule, and it decides every case. Enforcement runs on one
machine, so blocking is free. A second person approving a call does not run on one machine, so it is
not.

### Free, Apache-2.0 — the whole gate

The engine and the decision procedure. Every integration seam: MCP over stdio and HTTP, the Claude
Code `PreToolUse` hook, the in-process `Preflight`, and the native adapters. Observe mode **and
enforce mode**. `neti init`, `inventory`, `report`, `propose`, `verify`, `prove`, `score`. The record
chain and its verification. The local console, every screen.

A single developer, or a small team sharing a policy file in git, never needs anything else. Nothing
is withheld, nothing is time-limited, and there is no telemetry.

### Paid — the things one machine cannot do

- **Approvals.** `CONFIRM` means *a human other than the agent's operator must decide*. Asking that
  human requires somewhere for the request to go and somewhere for the answer to come back.
- **Organisation policy.** One version, signed, pinned by digest, across every agent. Without a
  server each machine has its own file, and they drift.
- **Budgets across the fleet.** Tallies survive a restart and span a day on their own now, but only
  *per machine* — so a declared "20,000 objects a day" is twenty thousand per laptop, and an
  organisation running forty agents declared a limit it does not have. One machine cannot know what
  the other thirty-nine did, which is the rule at the top of this file deciding the case.
  `SharedTallies` in `cloud.py` is the client, and `tests/integration/test_shared_tallies.py` pins
  every property it must keep — including the one that matters most: **while the control plane is
  unreachable it falls back to this machine's own total**, which is a lower bound on the fleet
  total. An outage under-counts, so a budget is missed rather than wrongly fired, and enforcement
  takes on no new availability risk. Stated plainly: during an outage a fleet budget is being
  enforced per machine, which is the free tier.
- **Audit and fleet across every agent.** Per-machine chains, anchored in one place, so a deleted
  local file becomes detectable.
- **The reviewed detection catalogue.** `neti init` gates what its rule table can claim, and that
  table is in this repository, readable and free. What one machine cannot have is everyone else's
  reviewed judgement about the rest of the MCP ecosystem — which tool parameter names a set somebody
  has already checked, and which resolver sizes it.

We are not inventing limitations to sell past. Every item above is a hole `SCOPE.md` already
documents, or work that only exists because more than one person did it.

## Four properties this split has to keep

1. **The client is open source.** `src/neti/cloud.py` — `HttpApprover`, `OrgClient`, the credential
   file, the wire format — is Apache-2.0 and in this repository, along with
   `tests/integration/test_approvals.py`, which pins every property a grant is allowed to have:
   bound to one call, single-use, expiring, and refused when the target has grown since a human
   looked at it. Read the protocol, write your own server, hold it to those tests. What you pay for
   is a server that is running, not a secret about how to talk to it.

2. **The control plane can only ever make a decision *more* permissive, and only through a named
   human.** If it is unreachable, absent, or unpaid, the gate behaves exactly as the free tier —
   `on_approval_unavailable` defaults to `block`, which is precisely what free does with a `CONFIRM`.
   Enforcement takes on no new availability risk by paying us.

3. **There are no licence checks in this code.** No key validation, no kill switch, no phone-home.
   The entitlement is *possession of a control plane*. `tests/property/test_licence_boundary.py`
   asserts that `neti` never imports `neti_cloud` and that no such package is in this repository, the
   same way `tests/property/test_core_is_pure.py` asserts `neti.core` reaches nothing outward.

4. **Neither half of the boundary can go quiet.** The other repository asserts the mirror image —
   that the control plane never imports the decision machinery, so a server-side ceiling comparison
   cannot appear and leave the audit record describing only one of two decisions. That test used to
   live here behind `if not PAID.exists(): return`, which the moment the split happened would have
   made it pass forever without reading a line of the code it checks. Each half now lives where the
   source it reads lives, and neither can become vacuous without that source disappearing.

## Your records stay yours

The local NDJSON is the source of truth. A control plane anchors it; it is not an authority over it.
`neti verify` works with the network unplugged, forever.

## Commercial use

The control plane is BUSL-1.1, converting to Apache-2.0 on 2030-07-31. The Additional Use Grant
permits unlimited internal use by one organisation. What it does not permit is offering the control
plane to third parties as a hosted service. If you want that, or a licence without the BUSL terms,
get in touch.
