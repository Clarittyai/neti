# Security

## Reporting

Open a [security advisory](https://github.com/Neti-Security/neti/security/advisories/new) rather than a
public issue. We will confirm within three working days.

## What `neti` is trusted with

Worth being precise, because the trust surface is smaller than it looks:

- **A read-only directory credential.** `GroupMember.Read.All` and nothing else. It counts group
  members and application assignments. It cannot write, cannot read mailboxes, cannot read files.
- **Tool-call arguments, in memory, on the machine the agent runs on.** They are used to resolve a
  target and are written into the local decision record. They are **not** sent to a control plane —
  it receives a digest and the evidence a reviewer needs, never the payload.
- **An organisation key**, if you use the paid tier. It can approve calls on your organisation's
  behalf. `neti login` writes it `0600`; treat it like an SSH key.

## What it is not

`neti` runs *after* authorization and answers one question: **how big is this?** It does not decide
whether the caller should be doing this at all (that is upstream), whether the action is correct
(NC-02), or contain anything after the fact (NC-11). The numbered non-coverage list in
[SCOPE.md](SCOPE.md) is exhaustive and `neti score` prints it.

A `neti` that is broken, misconfigured or switched off leaves you exactly where you were before you
installed it. It adds no privilege to an agent and holds no write capability of its own.

## Things we already know

- **The eventual-consistency window (NC-08).** Graph's `$count` is served from a secondary index
  that cannot be forced current. A magnitude is an auditable bound, not a guarantee of freshness.
  Approvals mitigate the worst of it by re-resolving on redemption and refusing a target that has
  grown past what a human approved.
- **The in-process seam can be forgotten.** `Preflight` only gates the calls you route through it,
  and nothing detects a tool you left out. The MCP and hook paths cannot be bypassed that way.
- **The control plane in this release is a POC.** One shared organisation key, no SSO, no per-user
  identity beyond the name a reviewer types. Do not expose it to the public internet.
