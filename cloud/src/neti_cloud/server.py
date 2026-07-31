"""The control plane's HTTP surface.

Small on purpose. There are two audiences and they want opposite things:

- **The gate** wants one question answered fast — is this call approved yet? It asks with a request
  digest and never sends the arguments themselves, only the evidence a reviewer needs.
- **A reviewer** wants an inbox: what is waiting, how big is it, approve or deny.

Auth is a bearer token per organisation. That is enough for a POC and deliberately not pretending to
be more: no SSO, no per-user identity beyond the name the reviewer states, no multi-tenancy. What it
does have is the part that would be dishonest to fake — the approval semantics are the real ones,
enforced in SQL, and the same test suite that pins the reference implementation runs against this.

Waiting is a long poll. The gate asks for up to `wait_s` and the server checks the row a few times a
second. Nothing here pushes, because a push channel would be a second delivery path to keep correct
and the notifiers already handle telling a human something is waiting.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from neti_cloud.notify import Notifier, NullNotifier
from neti_cloud.store import Store

__all__ = ["create_app"]

_POLL_S = 0.25
_MAX_WAIT_S = 300


class RequestApproval(BaseModel):
    digest: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    magnitude: int | None = None
    unit: str | None = None
    wait_s: float = 0.0


class Redeem(BaseModel):
    magnitude: int | None = None


class Decide(BaseModel):
    granted: bool
    decided_by: str
    reason: str | None = None


def create_app(
    store: Store | None = None, *, org_key: str = "dev-key", notifier: Notifier | None = None
) -> FastAPI:
    db = store or Store()
    notify = notifier or NullNotifier()
    app = FastAPI(title="neti control plane", docs_url="/docs")

    def authorised(authorization: str = Header(default="")) -> None:
        """One shared secret per organisation.

        Constant-time comparison because a bearer token compared with `==` leaks its prefix to
        anyone patient enough to measure, and this is the only thing standing between a stranger and
        the ability to approve calls.
        """
        import hmac

        presented = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(presented, org_key):
            raise HTTPException(401, "bad or missing organisation key")

    guard = [Depends(authorised)]

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        """Unauthenticated on purpose: `neti login` needs to tell "wrong key" from "wrong URL"."""
        return {"ok": True, "service": "neti-cloud"}

    # ---------------------------------------------------------------- the gate's side

    @app.get("/v1/approvals/by-digest/{digest}", dependencies=guard)
    def by_digest(digest: str) -> dict[str, Any]:
        found = db.open_for(digest)
        if found is None:
            raise HTTPException(404, "no open approval for that call")
        return found.as_json()

    @app.post("/v1/approvals", dependencies=guard)
    def request_approval(req: RequestApproval) -> dict[str, Any]:
        row = db.request(req.digest, req.evidence, req.magnitude, req.unit)
        if row.state == "pending" and row.decided_at is None:
            notify.notify(row)

        deadline = time.monotonic() + min(max(req.wait_s, 0.0), _MAX_WAIT_S)
        while row.state == "pending" and time.monotonic() < deadline:
            time.sleep(_POLL_S)
            row = db.get(row.id) or row

        # Still pending is a normal answer, not a timeout error: the gate hands the id to the agent
        # and the agent's retry finds the grant. Raising here would turn "a human is thinking" into
        # a failure the model has no way to act on.
        return row.as_json()

    @app.post("/v1/approvals/{approval_id}/redeem", dependencies=guard)
    def redeem(approval_id: str, req: Redeem) -> dict[str, Any]:
        try:
            return db.redeem(approval_id, req.magnitude).as_json()
        except KeyError as exc:
            raise HTTPException(404, f"no approval {approval_id}") from exc

    # ---------------------------------------------------------------- the reviewer's side

    @app.get("/v1/approvals", dependencies=guard)
    def list_approvals(state: str | None = None, limit: int = 100) -> dict[str, Any]:
        return {"approvals": [r.as_json() for r in db.list(state, limit)]}

    @app.post("/v1/approvals/{approval_id}/decide", dependencies=guard)
    def decide(approval_id: str, req: Decide) -> dict[str, Any]:
        decided = db.decide(
            approval_id, granted=req.granted, decided_by=req.decided_by, reason=req.reason
        )
        if decided is None:
            # Already answered, expired or spent. A 409 rather than a 404 because the row exists and
            # the reviewer should be told their decision arrived too late rather than that it
            # vanished.
            current = db.get(approval_id)
            if current is None:
                raise HTTPException(404, f"no approval {approval_id}")
            raise HTTPException(409, f"approval {approval_id} is already {current.state}")
        return decided.as_json()

    app.state.store = db
    return app
