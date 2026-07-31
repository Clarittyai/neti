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


def org_client() -> OrgClient | None:
    """An `OrgClient` if this machine is logged in, else `None` — which means the free tier."""
    creds = load_credentials()
    if creds is None or not creds.configured:
        return None
    return OrgClient(url=creds.url, key=creds.key)
