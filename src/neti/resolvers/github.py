"""How many repositories, and how many files, would this touch.

The GitHub MCP server is one of the most widely installed there is, and a token scoped to an
organisation turns `owner` into a string that addresses everything. Two resolvers, because two very
different ceilings are worth declaring:

- **`github.repos`** — `acme` resolves to every repository in the org; `acme/api` resolves to 1.
  This is the one that separates "delete a repo" from "delete the org's repos".
- **`github.files`** — `acme/api` resolves to the number of files on its default branch. This is
  what sizes a bulk rewrite, a branch delete, or an agent told to "clean up the repo".

**Both are one request, and only one of them is cheap.** Measured against the live API:

    github.repos  anthropics                        96 repos    exact          509ms
    github.files  anthropics/anthropic-sdk-python   1,291 files exact          908ms
    github.files  torvalds/linux                    67,653      lower_bound  5,684ms

`GET /orgs/{org}` is `$count`-class: a small body, inside the 800ms budget. The tree endpoint is
not. It is a single request that returns *the entire tree*, so a large repository means megabytes of
JSON and seconds of wall clock — one round trip is not the same claim as one cheap round trip, and
an earlier draft of this docstring asserted both were "inside the same latency budget as the Graph
`$count` resolvers" until the numbers above were taken. `github.files` is registered with a ten-
second timeout for that reason; treat it as bounded work like `fs.paths`, not as a counter.

**`truncated` is the whole reason the tree endpoint is usable.** GitHub caps that response and
*tells you* it did — the Linux row above is a real truncation, not a synthetic one — so a repository
too large to enumerate reports a `LOWER_BOUND` rather than a wrong number. Same shape as the storage
and filesystem caps: sound to block on, never sound to allow on.

**An owner may be a person.** `GET /orgs/{owner}` 404s for a personal account, so `torvalds` — a
perfectly ordinary target holding twelve repositories — resolved UNRESOLVED until the live check
caught it. It falls back to `GET /users/{owner}`, which answers for both.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol

from neti.core.types import Resolution
from neti.core.units import Direction, Unit
from neti.resolvers.base import ResolveContext

__all__ = ["GitHubApi", "GitHubFilesResolver", "GitHubReposResolver", "HttpGitHubApi"]


class GitHubApi(Protocol):
    """The provider seam. `path` is relative to the API root; returns the decoded JSON body."""

    def get(self, path: str) -> dict[str, Any]: ...


@dataclass
class HttpGitHubApi:
    """`GET` against api.github.com with a token. Needs `neti[graph]` for httpx.

    Not a GitHub SDK, for the same reason `GraphClient` is not a Graph SDK: the only operation a
    preflight gate needs is "read a count", and every additional verb is another failure mode to
    reason about inside an 800ms budget.
    """

    token: str | None = None
    """Read from `NETI_GITHUB_TOKEN` or `GITHUB_TOKEN` at call time when not given, so registering
    the resolvers costs nothing and a missing token surfaces as UNRESOLVED on the one gate that
    needed it rather than as a crash at startup."""

    base_url: str = "https://api.github.com"
    timeout_ms: int = 800

    def get(self, path: str) -> dict[str, Any]:
        import os

        import httpx

        token = self.token or os.environ.get("NETI_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise RuntimeError("github resolvers need NETI_GITHUB_TOKEN (or GITHUB_TOKEN)")

        response = httpx.get(
            f"{self.base_url.rstrip('/')}/{path.lstrip('/')}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "neti/0.1 (preflight gate)",
            },
            timeout=self.timeout_ms / 1000,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise TypeError(f"expected an object from {path}, got {type(body).__name__}")
        return body


def _split(target: str) -> tuple[str, str | None]:
    """`acme/api` -> ('acme', 'api'); `acme` -> ('acme', None)."""
    cleaned = target.strip().strip("/")
    if cleaned.startswith("https://github.com/"):
        cleaned = cleaned.removeprefix("https://github.com/")
    parts = [p for p in cleaned.split("/") if p]
    if len(parts) == 1:
        return parts[0], None
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", None


@dataclass
class GitHubReposResolver:
    """Repositories a target addresses: one for `owner/repo`, all of them for a bare `owner`."""

    api: GitHubApi

    owner: str | None = None
    """The org or user `neti inventory` should report as reachable, from `providers.github.owner`.

    Without it there is no bound to report: a token's real reach spans every organisation it is
    installed on, which is not something this resolver can enumerate and not a number anyone should
    invent. Declaring one turns the inventory row from `?` into "this token reaches 96 repositories
    in one call", which is the day-one finding.
    """

    unit: ClassVar[Unit] = Unit.REPOSITORIES
    breakdown_keys: ClassVar[frozenset[str]] = frozenset({"private"})

    def resolve(self, target: str, ctx: ResolveContext) -> Resolution:
        del ctx
        owner, repo = _split(target)
        if not owner:
            return Resolution.unresolved(
                self.unit,
                reason="not_a_github_target",
                evidence={"target": target[:200], "expected": "owner or owner/repo"},
            )

        if repo is not None:
            # Named explicitly, so it is exactly one and no request is needed. Deliberately not
            # verified to exist: a gate's job is to size the call, and a repository that is not
            # there fails on its own without costing a round trip on every single tool call.
            return self._resolved(
                1, {"private": 0}, Direction.EXACT, {"owner": owner, "repo": repo}
            )

        # Orgs first, because only that endpoint reports `total_private_repos` — a count that
        # omitted private repositories would be an under-count, which is the dangerous direction.
        # Then users, because an owner is very often a person and `/orgs/{person}` is a 404.
        basis = "orgs endpoint"
        try:
            body = self.api.get(f"/orgs/{owner}")
        except Exception as org_error:
            try:
                body = self.api.get(f"/users/{owner}")
                basis = "users endpoint"
            except Exception as user_error:
                # Neither: an owner we cannot read is not an owner with no repositories.
                return Resolution.unresolved(
                    self.unit,
                    reason="lookup_failed",
                    evidence={
                        "error": str(user_error)[:200],
                        "org_error": str(org_error)[:120],
                        "owner": owner,
                    },
                )

        public = int(body.get("public_repos") or 0)

        # `total_private_repos` is only populated when the token can see inside the account. It came
        # back `null` for a real org on the live check, and the resolver reported EXACT 96 for an
        # organisation that certainly has private repositories — a wrong EXACT, which is the one
        # kind of wrong this product must not produce. Present means we saw everything; absent means
        # private repositories exist and are invisible, so the count is a floor.
        declared_private = body.get("total_private_repos")
        private = int(declared_private or 0)
        complete = declared_private is not None

        return self._resolved(
            public + private,
            {"private": private},
            Direction.EXACT if complete else Direction.LOWER_BOUND,
            {
                "owner": owner,
                "basis": basis,
                "type": body.get("type") or "Organization",
                "private_visible": complete,
            },
        )

    def reachable_max(self, ctx: ResolveContext) -> Resolution:
        if self.owner is None:
            return Resolution.unresolved(
                self.unit,
                reason="no_owner_declared",
                evidence={
                    "hint": (
                        "what this token could reach spans every org it is installed on, which is "
                        "not a bound this resolver can enumerate. Declare `providers.github.owner` "
                        "to report the one you care about."
                    )
                },
            )
        # The same request `resolve` makes for a bare owner, which is why this needs no separate
        # code path and inherits the same honesty about invisible private repositories.
        return self.resolve(self.owner, ctx)

    def _resolved(
        self,
        count: int,
        breakdown: dict[str, int],
        direction: Direction,
        evidence: dict[str, Any],
    ) -> Resolution:
        return Resolution.resolved(
            self.unit,
            count,
            direction=direction,
            resolved_at=datetime.now(UTC),
            consistency="eventual",
            breakdown=breakdown,
            evidence=evidence,
        )


@dataclass
class GitHubFilesResolver:
    """Files on a repository's default branch, from one recursive tree request."""

    api: GitHubApi

    unit: ClassVar[Unit] = Unit.OBJECTS
    breakdown_keys: ClassVar[frozenset[str]] = frozenset({"bytes"})

    def resolve(self, target: str, ctx: ResolveContext) -> Resolution:
        del ctx
        owner, repo = _split(target)
        if not owner or repo is None:
            # A bare org has no single tree to count. Sizing it would mean walking every repository,
            # which is not one request and not this resolver's job — `github.repos` is.
            return Resolution.unresolved(
                self.unit,
                reason="not_a_repository",
                evidence={"target": target[:200], "expected": "owner/repo"},
            )

        try:
            meta = self.api.get(f"/repos/{owner}/{repo}")
            branch = str(meta.get("default_branch") or "main")
            tree = self.api.get(f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
        except Exception as exc:
            return Resolution.unresolved(
                self.unit,
                reason="tree_failed",
                evidence={"error": str(exc)[:200], "owner": owner, "repo": repo},
            )

        entries = tree.get("tree") or []
        blobs = [e for e in entries if e.get("type") == "blob"]
        total_bytes = sum(int(e.get("size") or 0) for e in blobs)
        truncated = bool(tree.get("truncated"))

        return Resolution.resolved(
            self.unit,
            len(blobs),
            # GitHub caps the tree response and says so. A capped answer is a floor, which blocks
            # soundly and cannot allow — so the repositories too big to count are precisely the
            # ones that cannot slip through under a ceiling.
            direction=Direction.LOWER_BOUND if truncated else Direction.EXACT,
            resolved_at=datetime.now(UTC),
            consistency="eventual",
            breakdown={"bytes": total_bytes},
            evidence={
                "owner": owner,
                "repo": repo,
                "branch": branch,
                "truncated": truncated,
            },
        )

    def reachable_max(self, ctx: ResolveContext) -> Resolution:
        del ctx
        return Resolution.unresolved(
            self.unit,
            reason="no_repository_declared",
            evidence={"hint": "bind this resolver to a parameter naming owner/repo"},
        )
