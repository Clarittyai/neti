"""Microsoft Graph client for count reads.

Everything here exists to turn provider reality into one of three honest answers. The design notes
that matter:

**One request, not a pagination loop.** `GET /groups/{id}/transitiveMembers/$count` returns
`text/plain` with an integer. A 41,203-member group costs the same as a 3-member group, which is the
entire reason this project targets Graph rather than Google Directory — there, a count means ~206
sequential pages and the gate is slowest exactly when the action is most dangerous. It is also why
`PARTIAL` is nearly unreachable on this path: you cannot half-read an integer.

**Positive assertion, in one place.** Graph silently ignores `?$count=true` when the
`ConsistencyLevel: eventual` header is absent. A client that trusts a 200 and reads a missing count
as zero would allow every call. So `_read_count` believes a response only if the status, the
content type and the body all agree, and treats anything else as UNRESOLVED.

**Token acquisition is never on the hot path.** It costs 200-700ms — comparable to the entire
resolution budget. The cache refreshes ahead of expiry in a background thread while continuing to
serve the still-valid token.

**There is no snapshot token.** Graph exposes no etag or consistency marker for `$count`, so
`provider_snapshot` is honestly `None` and the request id goes in `evidence` instead. A stored
decision replays exactly; the *number* it was based on is not reproducible against an
eventually-consistent index. That distinction is the whole content of "an auditable bound, not
freshness" and it should not be papered over with a field that looks like a snapshot but is not.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

__all__ = ["ClientCredential", "CountOutcome", "GraphClient", "GraphError"]

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"

# Refresh this far into the token's life. 0.8 leaves ~11 minutes of slack on a typical 60-minute
# token, which is far more than any transient login-endpoint outage we need to ride out.
_REFRESH_AT = 0.8


class GraphError(RuntimeError):
    """A configuration or credential failure the caller cannot proceed from."""


@dataclass
class ClientCredential:
    tenant_id: str
    client_id: str
    client_secret: str


@dataclass
class CountOutcome:
    """The result of one count read, before it becomes a `Resolution`.

    `ok` and `magnitude` are deliberately separate from `partial`/`unsupported` so the resolver can
    choose the right three-state answer rather than this layer guessing.
    """

    ok: bool
    magnitude: int | None = None
    unsupported: bool = False
    """Target exists but is structurally uncountable here — an Exchange dynamic distribution
    group. Must never become 0."""

    reason: str = ""
    status: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


class _TokenCache:
    """Client-credentials token with refresh-ahead.

    The refresh runs in a daemon thread and the current token keeps being served until the new one
    lands, so no request ever waits on token acquisition unless the cache is cold.
    """

    def __init__(self, credential: ClientCredential, client: httpx.Client) -> None:
        self._cred = credential
        self._client = client
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._refresh_at: float = 0.0
        self._refreshing = False

    def token(self) -> str:
        now = time.monotonic()
        with self._lock:
            token, expires_at, refresh_at = self._token, self._expires_at, self._refresh_at
            should_refresh = token is not None and now >= refresh_at and not self._refreshing
            if should_refresh:
                self._refreshing = True

        if token is not None and now < expires_at:
            if should_refresh:
                threading.Thread(target=self._refresh_quietly, daemon=True).start()
            return token

        return self._acquire()

    def _refresh_quietly(self) -> None:
        try:
            self._acquire()
        except Exception:
            pass
        finally:
            with self._lock:
                self._refreshing = False

    def _acquire(self) -> str:
        resp = self._client.post(
            f"{LOGIN}/{self._cred.tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": self._cred.client_id,
                "client_secret": self._cred.client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
        if resp.status_code != 200:
            raise GraphError(f"token request failed {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise GraphError("token response carried no access_token")
        lifetime = float(payload.get("expires_in", 3600))
        now = time.monotonic()
        with self._lock:
            self._token = str(token)
            self._expires_at = now + lifetime
            self._refresh_at = now + lifetime * _REFRESH_AT
        return str(token)


class GraphClient:
    """Thin, synchronous, count-shaped. Not a general Graph SDK on purpose.

    The only operation is "read an integer from a `$count` endpoint", because that is the only
    operation a preflight gate needs and every additional verb is another failure mode to reason
    about inside an 800ms budget.
    """

    def __init__(
        self,
        credential: ClientCredential,
        *,
        timeout_ms: int = 800,
        transport: httpx.BaseTransport | None = None,
        base_url: str = GRAPH,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout_ms = timeout_ms
        self._client = httpx.Client(
            http2=transport is None,
            timeout=timeout_ms / 1000,
            transport=transport,
            headers={"User-Agent": "neti/0.1 (preflight gate)"},
        )
        self._tokens = _TokenCache(credential, self._client)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ counts

    def count(self, path: str, *, params: dict[str, str] | None = None) -> CountOutcome:
        """Read a `$count` endpoint. Never raises for provider failure.

        Retries exactly once, and only for the two statuses where a retry is meaningful (429, 5xx),
        and only if the server's own `Retry-After` fits inside the remaining budget. A gate that
        retries harder than that has stopped being a gate: the agent is blocked on us the whole
        time, and a slow allow is worse than a fast unresolved.
        """
        deadline = time.monotonic() + self._timeout_ms / 1000
        attempt = self._attempt(path, params)
        if attempt.ok or attempt.unsupported:
            return attempt

        if attempt.status in (429, 500, 502, 503, 504):
            wait = self._retry_delay(attempt)
            if wait is not None and time.monotonic() + wait < deadline:
                time.sleep(wait)
                retried = self._attempt(path, params)
                retried.evidence["retried_after_status"] = attempt.status
                return retried

        return attempt

    @staticmethod
    def _retry_delay(outcome: CountOutcome) -> float | None:
        raw = outcome.evidence.get("retry_after")
        if raw is None:
            return 0.05 if outcome.status != 429 else None
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return None

    def _attempt(self, path: str, params: dict[str, str] | None) -> CountOutcome:
        url = f"{self._base}{path}"
        try:
            token = self._tokens.token()
        except GraphError as exc:
            return CountOutcome(ok=False, reason=f"credential: {exc}")

        try:
            resp = self._client.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    # Mandatory. Without it Graph errors on /$count as a path segment and, worse,
                    # silently ignores ?$count=true as a query parameter.
                    "ConsistencyLevel": "eventual",
                },
            )
        except httpx.TimeoutException:
            return CountOutcome(ok=False, reason="timeout", evidence={"url": url})
        except httpx.HTTPError as exc:
            return CountOutcome(ok=False, reason=f"transport: {exc}", evidence={"url": url})

        return self._read_count(resp, url)

    @staticmethod
    def _read_count(resp: httpx.Response, url: str) -> CountOutcome:
        """The positive assertion. Believe a response only if every signal agrees."""
        evidence: dict[str, Any] = {
            "url": url,
            "status": resp.status_code,
            "request_id": resp.headers.get("request-id"),
            "date": resp.headers.get("date"),
        }
        if (ru := resp.headers.get("x-ms-resource-unit")) is not None:
            evidence["resource_units"] = ru
        if (retry_after := resp.headers.get("retry-after")) is not None:
            evidence["retry_after"] = retry_after

        if resp.status_code == 404:
            # Two very different situations behind one status. A group that was deleted is a
            # transient-ish operator problem; a dynamic distribution group is a permanent structural
            # gap (Exchange computes its membership and never syncs it to Entra). The resolver
            # decides which; both are UNRESOLVED and neither is ever 0.
            return CountOutcome(
                ok=False, status=404, reason="not_found_or_not_an_entra_group", evidence=evidence
            )
        if resp.status_code in (401, 403):
            return CountOutcome(
                ok=False,
                status=resp.status_code,
                reason="unauthorised: check GroupMember.Read.All is granted and admin-consented",
                evidence=evidence,
            )
        if resp.status_code != 200:
            return CountOutcome(
                ok=False,
                status=resp.status_code,
                reason=f"http_{resp.status_code}",
                evidence=evidence | {"body": resp.text[:200]},
            )

        ctype = resp.headers.get("content-type", "")
        if "text/plain" not in ctype:
            # The documented silent failure: a JSON collection came back instead of a count,
            # which means the count was ignored rather than computed.
            return CountOutcome(
                ok=False,
                status=200,
                reason=f"expected text/plain count, got {ctype!r}",
                evidence=evidence | {"body": resp.text[:200]},
            )

        body = resp.text.strip()
        if not body.isdigit():
            return CountOutcome(
                ok=False,
                status=200,
                reason="count body is not an integer",
                evidence=evidence | {"body": body[:200]},
            )

        return CountOutcome(ok=True, magnitude=int(body), status=200, evidence=evidence)


def utc_now() -> datetime:
    """Timestamps live out here, never in `neti.core`."""
    return datetime.now(UTC)
