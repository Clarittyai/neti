"""Talking to a control plane, from the free side.

This is Apache-2.0 and it is the *client*. It has no licence check in it and never will: it is
perfectly complete, perfectly open, and does exactly nothing without a server to talk to. That is
the whole entitlement model — you are not paying for a key that unlocks code you already have, you
are paying for shared state that one machine cannot have. See LICENSING.md.

Where the server lives is deployment configuration, so it is read from `~/.neti/credentials.toml`
and the environment rather than from the policy. The policy digest has to keep meaning *these
ceilings* rather than *this environment*, or the same decision made on two machines would be
recorded as two different policies. The one approval setting that genuinely does change decisions —
`on_approval_unavailable` — is in the policy, where it belongs.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from neti.approvals import Approval, ApprovalState, ApproverError
from neti.core.types import ProposedCall

__all__ = [
    "Credentials",
    "HttpApprover",
    "OrgClient",
    "SharedTallies",
    "credentials_path",
    "load_credentials",
    "org_client",
    "save_credentials",
]

DEFAULT_WAIT_S = 45.0
"""How long the gate waits for a human before telling the agent to retry.

Chosen against MCP client timeouts rather than against human attention spans. A reviewer takes
minutes; a tool call gets tens of seconds. Waiting past the client's own limit would convert a
pending approval into a transport error, which is strictly worse than a pending answer the model can
read and act on.
"""


def credentials_path() -> Path:
    return Path(os.environ.get("NETI_HOME", Path.home() / ".neti")) / "credentials.toml"


@dataclass(frozen=True)
class Credentials:
    url: str
    key: str
    org: str = "default"

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key)


def load_credentials() -> Credentials | None:
    """From the environment first, then the file.

    Environment wins so CI and containers can point at a control plane without writing to a home
    directory that may not survive the process.
    """
    url = os.environ.get("NETI_CLOUD_URL")
    key = os.environ.get("NETI_CLOUD_KEY")
    if url and key:
        return Credentials(url=url, key=key, org=os.environ.get("NETI_CLOUD_ORG", "default"))

    path = credentials_path()
    try:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    cloud = data.get("cloud") or {}
    if not (cloud.get("url") and cloud.get("key")):
        return None
    return Credentials(
        url=str(cloud["url"]), key=str(cloud["key"]), org=str(cloud.get("org", "default"))
    )


def save_credentials(creds: Credentials) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Written by `neti login`. This is deployment config, not policy — nothing here\n"
        "# changes a verdict, so none of it enters the policy digest.\n"
        "[cloud]\n"
        f'url = "{creds.url}"\n'
        f'key = "{creds.key}"\n'
        f'org = "{creds.org}"\n',
        encoding="utf-8",
    )
    # The key can approve calls on behalf of the organisation. Treat it like an SSH key.
    path.chmod(0o600)
    return path


@dataclass
class HttpApprover:
    """The `Approver` protocol over HTTP.

    Every failure becomes `ApproverError`, and never a denial. The gate turns an `ApproverError`
    into `on_approval_unavailable` — which defaults to `block`, i.e. exactly what an install with no
    control plane does. Collapsing "nobody could be asked" into "somebody said no" would make an
    outage indistinguishable from a policy decision in the audit record.
    """

    url: str
    key: str
    wait_s: float = DEFAULT_WAIT_S
    timeout_s: float = 10.0

    def __post_init__(self) -> None:
        import httpx

        self._client = httpx.Client(
            base_url=self.url.rstrip("/"),
            headers={"Authorization": f"Bearer {self.key}"},
            # The request timeout has to outlast the wait, or the long poll would abort itself.
            timeout=max(self.timeout_s, self.wait_s + 10),
        )

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ Approver

    def find(self, digest: str) -> Approval | None:
        response = self._get(f"/v1/approvals/by-digest/{digest}")
        if response is None:
            return None
        return _approval(response)

    def request(self, call: ProposedCall, digest: str, evidence: dict[str, Any]) -> Approval:
        # The arguments stay on the machine. A reviewer needs the magnitude, not the payload.
        del call
        body = {
            "digest": digest,
            "evidence": evidence,
            "magnitude": evidence.get("magnitude"),
            "unit": evidence.get("unit"),
            "wait_s": self.wait_s,
        }
        return _approval(self._post("/v1/approvals", body))

    def redeem(self, approval: Approval, magnitude: int | None) -> Approval:
        return _approval(
            self._post(f"/v1/approvals/{approval.id}/redeem", {"magnitude": magnitude})
        )

    # ------------------------------------------------------------------ internals

    def _get(self, path: str) -> dict[str, Any] | None:
        import httpx

        try:
            response = self._client.get(path)
        except httpx.HTTPError as exc:
            raise ApproverError(f"control plane unreachable: {exc}") from exc
        if response.status_code == 404:
            return None  # "no such approval" is an answer, not a failure
        return self._body(response)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        import httpx

        try:
            response = self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise ApproverError(f"control plane unreachable: {exc}") from exc
        result = self._body(response)
        if result is None:
            raise ApproverError(f"control plane returned nothing for {path}")
        return result

    @staticmethod
    def _body(response: Any) -> dict[str, Any] | None:
        if response.status_code == 401:
            raise ApproverError("control plane rejected the organisation key")
        if response.status_code >= 400:
            raise ApproverError(f"control plane returned {response.status_code}")
        parsed: dict[str, Any] = response.json()
        return parsed


def _approval(payload: dict[str, Any]) -> Approval:
    return Approval(
        id=str(payload["id"]),
        state=ApprovalState(payload["state"]),
        digest=str(payload["digest"]),
        approved_magnitude=payload.get("approved_magnitude"),
        unit=payload.get("unit"),
        decided_by=payload.get("decided_by"),
        decided_at=payload.get("decided_at"),
        reason=payload.get("reason"),
        expires_at=payload.get("expires_at"),
    )


@dataclass
class OrgClient:
    """The reviewer's side of the control plane, for the console to sit in front of.

    Separate from `HttpApprover` because the two have opposite shapes. The approver answers one
    question about one call as fast as it can; this lists an inbox and posts decisions into it.
    Folding them together would give the gate's hot path methods it must never call.

    It lives in the Apache-2.0 package for the same reason the approver does: it is a client, it is
    inert without a server, and the console must be able to show a Team section without the free
    tier acquiring a dependency on the paid one.
    """

    url: str
    key: str
    timeout_s: float = 10.0

    def __post_init__(self) -> None:
        import httpx

        self._client = httpx.Client(
            base_url=self.url.rstrip("/"),
            headers={"Authorization": f"Bearer {self.key}"},
            timeout=self.timeout_s,
        )

    def close(self) -> None:
        self._client.close()

    def health(self) -> bool:
        import httpx

        try:
            response = self._client.get("/v1/health")
        except httpx.HTTPError:
            return False
        return response.status_code == 200 and bool(response.json().get("ok"))

    def approvals(self, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        params = {"limit": limit}
        if state:
            params["state"] = state  # type: ignore[assignment]
        rows: list[dict[str, Any]] = self._json("GET", "/v1/approvals", params=params)["approvals"]
        return rows

    def decide(
        self, approval_id: str, *, granted: bool, decided_by: str, reason: str | None = None
    ) -> dict[str, Any]:
        return self._json(
            "POST",
            f"/v1/approvals/{approval_id}/decide",
            json={"granted": granted, "decided_by": decided_by, "reason": reason},
        )

    # ------------------------------------------------------------------ shared budget totals
    #
    # Two calls, and deliberately no vocabulary of their own: a bucket key is the string
    # `neti.store.sessions.bucket_key` already produces for the local sidecar, and a total is the
    # same `{unit: integer}` shape `SessionTally` stores. A server that speaks these two endpoints
    # is a complete implementation.

    def totals(self, bucket: str) -> dict[str, Any]:
        """What the organisation has accumulated in this bucket.

        **A 404 raises rather than reading as an empty bucket**, and getting that backwards is the
        more dangerous of the two options. It was written the other way first, reasoning that the
        first call of a new day asks for a bucket nobody has written yet — true, and it makes a
        server that does *not implement this route at all* indistinguishable from one that does.
        `neti-cloud` today serves approvals and health and nothing else, so every `GET` would
        have returned "empty", every fleet total would have read **zero**, and a budget would
        have been compared against a number lower than what this machine alone had already done.

        Raising sends `SharedTallies` to the local store instead, which is a floor rather than a
        guess. On a server that does implement the route, a genuinely new bucket falls back to this
        machine's own contribution — at most the fleet total, never more — and self-corrects on the
        next write. Both readings under-count; only one under-counts by *everything*.
        """
        return self._json("GET", f"/v1/totals/{bucket}")

    def add_totals(self, bucket: str, contribution: dict[str, int]) -> dict[str, Any]:
        """Add this call's magnitudes and return the new organisation total.

        Add-and-read in one request on purpose. Two round trips would race exactly the way
        `SessionStore.add` used to before it read and wrote under one lock — two agents both read
        3, both write 4, and one call is forgotten. The increment has to be the server's job.
        """
        return self._json("POST", f"/v1/totals/{bucket}", json={"add": contribution})

    def _json(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        import httpx

        try:
            response = self._client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise ApproverError(f"control plane unreachable: {exc}") from exc
        if response.status_code == 401:
            raise ApproverError("control plane rejected the organisation key")
        if response.status_code == 409:
            # Somebody else got there first. A distinct message because "already answered" is a
            # thing the reviewer needs told, not a generic failure.
            raise ApproverError(response.json().get("detail", "already decided"))
        if response.status_code >= 400:
            raise ApproverError(f"control plane returned {response.status_code}")
        parsed: dict[str, Any] = response.json()
        return parsed


@dataclass
class SharedTallies:
    """Budget totals pooled across every machine in the organisation.

    **The hole it closes.** `SessionStore` made a budget survive a restart, and windows made one
    span a day — but both are still *this machine*. A declared "20,000 objects a day" is twenty
    thousand per laptop, so an org running forty agents declared a limit it does not have. That is
    the last shape of `SCOPE.md` NC-01 that a single machine structurally cannot see, and it is why
    `LICENSING.md` lists shared budgets as paid: the rule there is *can one machine do this?*, and
    one machine cannot know what the other thirty-nine did.

    **Same four methods as `SessionStore` and `MemoryTallies`**, so the engine does not branch. It
    duck-types `sessions` for exactly this reason.

    ---

    Two properties this must keep, and both are about *not* becoming a new way to fail.

    **1. An outage degrades to the free tier; it never blocks more.** Every remote call falls back
    to the local store, which is a floor rather than a guess: it holds what *this* machine did, so
    the fallback total is a lower bound on the fleet total. Under-counting costs a missed budget.
    Over-counting would cost a wrongly blocked call, and a control plane that can stop work by being
    unreachable is precisely what `LICENSING.md` promises paying for does not buy you. Stated
    plainly rather than implied: **while the control plane is unreachable, a fleet budget is not
    being enforced across the fleet.** It is being enforced per machine, which is the free tier.

    **2. The local write always happens.** Posting remotely and skipping the local record would mean
    an outage started the fallback from zero — so the first minute of every outage would forget
    everything the machine had already done. Local first, remote second, and the remote answer wins
    only when there is one.

    The wire is four calls and no vocabulary of its own. Read it, write a server, and hold it to
    `tests/integration/test_shared_tallies.py`, which pins every property above. What the hosted
    tier sells is a server that is running, not a secret about how to talk to it.
    """

    local: Any
    """A `SessionStore`. The floor, and the whole fallback path."""

    client: OrgClient
    agent: str = "default"
    """Which machine this is, for the control plane's own reporting. Never part of a bucket key —
    the point of a shared total is that every agent counts into the same one."""

    _warned: bool = False
    """Whether this process has already said that budgets are counting per machine."""

    def load(self, window: Any, session_id: str, now: float) -> Any:
        from neti.core.budget import SessionTally
        from neti.store.sessions import bucket_key

        if _is_session_window(window):
            # A `session` window is one conversation on one machine. Pooling it across the fleet
            # would add up unrelated conversations that merely share an id, which is not a total
            # anybody declared. Only the wider windows are org-scoped.
            return self.local.load(window, session_id, now)

        key = bucket_key(window, session_id, now)
        try:
            body = self.client.totals(key)
        except ApproverError as exc:
            self._degraded(exc)
            return self.local.load(window, session_id, now)
        totals = {str(k): int(v) for k, v in (body.get("totals") or {}).items()}
        return SessionTally(totals=totals, calls=int(body.get("calls") or 0))

    def _degraded(self, exc: ApproverError) -> None:
        """Say once, on stderr, that a fleet budget is currently a per-machine one.

        Silence here is the failure this project keeps finding under a different name. An operator
        who passed `--org` believes their twenty-thousand-a-day is twenty thousand across the fleet;
        if the control plane cannot answer, it is twenty thousand *per machine* and nothing anywhere
        says so. That is dead config that reads as configured.

        Once, not per call: `neti hook` is a fresh process each time, so a per-call warning would be
        one line of stderr for every tool the agent runs. Per process is the most this can say
        without becoming the noise that gets a gate switched off.
        """
        if self._warned:
            return
        object.__setattr__(self, "_warned", True)
        print(
            f"neti: shared budgets unavailable ({exc}). Counting per machine until the control "
            "plane answers — a declared fleet budget is not being enforced across the fleet.",
            file=sys.stderr,
        )

    def add(self, window: Any, session_id: str, now: float, args: tuple[Any, ...]) -> Any:
        from neti.store.sessions import bucket_key

        # Local first, always — see property 2 above.
        local_total = self.local.add(window, session_id, now, args)
        if _is_session_window(window):
            return local_total

        from neti.core.budget import SessionTally
        from neti.core.verdict import ResolutionState

        contribution: dict[str, int] = {}
        for arg in args:
            res = arg.resolution
            if res.state is ResolutionState.RESOLVED and res.magnitude is not None:
                contribution[res.unit.value] = contribution.get(res.unit.value, 0) + res.magnitude
        try:
            body = self.client.add_totals(bucket_key(window, session_id, now), contribution)
        except ApproverError as exc:
            self._degraded(exc)
            return local_total
        totals = {str(k): int(v) for k, v in (body.get("totals") or {}).items()}
        return SessionTally(totals=totals, calls=int(body.get("calls") or 0))

    # A taint is a fact about one conversation on one machine, so it stays local. Sharing it would
    # mean an agent that read a support ticket here is downstream of it in an unrelated
    # conversation on somebody else's laptop, which is not what provenance claims.
    def load_taint(self, session_id: str) -> Any:
        return self.local.load_taint(session_id)

    def remember_taint(self, session_id: str, taint: Any) -> None:
        self.local.remember_taint(session_id, taint)

    def sweep(self) -> None:
        self.local.sweep()


def _is_session_window(window: Any) -> bool:
    from neti.core.budget import WindowKind

    return getattr(window, "kind", None) is WindowKind.SESSION


def org_client() -> OrgClient | None:
    """An `OrgClient` if this machine is logged in, else `None` — which means the free tier."""
    creds = load_credentials()
    if creds is None or not creds.configured:
        return None
    return OrgClient(url=creds.url, key=creds.key)
