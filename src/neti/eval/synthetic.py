"""A synthetic Entra tenant as an `httpx.MockTransport`.

Product surface rather than a test fixture, despite where it started. `neti demo` runs the full
narrative against it, so the numbers in the demo are produced by the real decision path rather than
written into a slide — which is the only way a demo stays true as the code changes.

Ground truth is exact *by construction*: the fixture declares that `eng-all` has 41,203 transitive
members, so the assertion "the resolver returned 41,203" tests the resolver rather than a guess
about a real directory. That is the property M1 depends on, and it is why the correctness suite does
not need tenant credentials.

It reproduces the provider behaviours that actually matter, especially the ones that fail *quietly*:

- `$count` returns `text/plain` with a bare integer, not JSON.
- Omitting `ConsistencyLevel: eventual` makes `/$count` error and makes `?$count=true` silently
  ignored — the documented fail-open that RESOLVER_CONTRACT.md rule 4 exists to catch.
- An Exchange dynamic distribution group 404s on every endpoint, because Exchange computes its
  membership and never syncs it to Entra.
- 429 carries `Retry-After`; throttled and server-error responses are JSON, not `text/plain`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

TOKEN_URL_PART = "/oauth2/v2.0/token"


@dataclass
class Group:
    group_id: str
    display_name: str
    transitive_members: int
    guests: int = 0
    app_assignments: int = 0
    kind: str = "security"


@dataclass
class SyntheticTenant:
    """Declared ground truth, plus switches for the failure-mode matrix."""

    groups: dict[str, Group] = field(default_factory=dict)
    total_users: int = 52_400
    total_service_principals: int = 214

    # failure injection
    fail_next: list[int] = field(default_factory=list)
    """Statuses to return, one per call, before normal behaviour resumes."""

    fail_when: str | None = None
    """Restrict `fail_next` to URLs containing this substring.

    Needed because a resolution can involve more than one request — the guest breakdown issues a
    total count and then a filtered count — and an unscoped queue silently fails the wrong one.
    """

    token_status: int = 200
    force_json_count: bool = False
    """Return a JSON collection from a $count path — the 'silently ignored' shape."""

    force_truncated: bool = False
    """Return a JSON page carrying @odata.nextLink, i.e. a truncated enumeration."""

    calls: list[str] = field(default_factory=list)
    headers_seen: list[dict[str, str]] = field(default_factory=list)

    def add(self, group: Group) -> SyntheticTenant:
        self.groups[group.group_id] = group
        return self

    # ------------------------------------------------------------------ transport

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        self.headers_seen.append(dict(request.headers))

        if TOKEN_URL_PART in url:
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": "synthetic-token", "expires_in": 3600})

        if self.fail_next and (self.fail_when is None or self.fail_when in url):
            status = self.fail_next.pop(0)
            headers = {"Retry-After": "0"} if status == 429 else {}
            return httpx.Response(status, json={"error": {"code": str(status)}}, headers=headers)

        # The two `$count` forms fail *differently* without `ConsistencyLevel`, and the difference
        # is the whole point of the HEADER probe:
        #   - `/$count` as a path segment errors, loudly.
        #   - `?$count=true` as a query parameter is silently ignored — a 200 with no count, which
        #     is the shape a careless client reads as zero.
        has_header = request.headers.get("ConsistencyLevel") == "eventual"
        if request.url.path.endswith("/$count") and not has_header:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "Request_UnsupportedQuery",
                        "message": "$count is not currently supported without ConsistencyLevel",
                    }
                },
            )
        if request.url.params.get("$count") and not has_header:
            return httpx.Response(200, json={"value": [{"id": "x"}]})

        path = request.url.path

        if path == "/v1.0/users/$count":
            return self._count(self.total_users)
        if path == "/v1.0/servicePrincipals/$count":
            return self._count(self.total_service_principals)

        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[1] == "groups":
            return self._group_route(parts, request)

        return httpx.Response(404, json={"error": {"code": "ResourceNotFound"}})

    def _group_route(self, parts: list[str], request: httpx.Request) -> httpx.Response:
        group_id = parts[2]
        group = self.groups.get(group_id)
        if group is None:
            return httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound"}})

        # An Exchange DDG is a real, resolvable-looking mail target that Graph simply cannot count.
        if group.kind == "dynamic_distribution":
            return httpx.Response(
                404,
                json={
                    "error": {
                        "code": "Request_ResourceNotFound",
                        "message": f"Resource '{group_id}' does not exist.",
                    }
                },
            )

        tail = parts[3:]
        if tail[:1] == ["transitiveMembers"]:
            if "microsoft.graph.user" in tail:
                return self._count(group.guests)
            return self._count(group.transitive_members)
        if tail[:1] == ["appRoleAssignments"]:
            return self._count(group.app_assignments)
        if not tail:
            return httpx.Response(
                200,
                json={
                    "id": group.group_id,
                    "displayName": group.display_name,
                    "groupTypes": [] if group.kind == "security" else [group.kind],
                    "mailEnabled": group.kind != "security",
                },
            )
        return httpx.Response(404, json={"error": {"code": "ResourceNotFound"}})

    def _count(self, value: int) -> httpx.Response:
        if self.force_truncated:
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "x"}],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/next-page",
                },
            )
        if self.force_json_count:
            # The dangerous shape: 200 OK, JSON collection, no count anywhere.
            return httpx.Response(200, json={"value": [{"id": "x"}]})
        return httpx.Response(
            200,
            text=str(value),
            headers={
                "Content-Type": "text/plain",
                "request-id": "req-1",
                "x-ms-resource-unit": "1",
            },
        )


def default_tenant() -> SyntheticTenant:
    """The fixture the correctness suite asserts against. Sizes span four orders of magnitude so
    the flat-latency and exact-resolution claims are tested across the range that matters."""
    return (
        SyntheticTenant()
        .add(Group("g-solo", "Founders", transitive_members=1, app_assignments=0))
        .add(Group("g-team", "Platform Team", transitive_members=25, guests=2, app_assignments=3))
        .add(Group("g-dept", "Engineering", transitive_members=500, guests=11, app_assignments=9))
        .add(
            Group(
                "g-eng-all",
                "All Engineering (nested)",
                transitive_members=41_203,
                guests=412,
                app_assignments=37,
            )
        )
        .add(
            Group(
                "g-ddg",
                "All Customers (dynamic distribution)",
                transitive_members=38_014,
                kind="dynamic_distribution",
            )
        )
    )
