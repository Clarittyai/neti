"""The GitHub resolvers, against a recorded API.

The behaviours that carry weight are the ones that carry weight everywhere else in this product: a
bare owner must not resolve like a single repository, a truncated answer must be a floor rather than
a number, and a count that could not see everything must not claim to be exact.

Three of the tests below exist because `tests/live/test_github_live.py` found the defects first and
this file could not have. Offline tests assert what happens *given* a shape; only a live run tells
you the shape is real — that `/orgs/{person}` is a 404, that `total_private_repos` comes back null,
that a large tree takes seconds.
"""

from __future__ import annotations

from typing import Any

import pytest

from neti.core.units import Direction, Unit, may_allow, may_block
from neti.core.verdict import ResolutionState
from neti.resolvers.base import ResolveContext
from neti.resolvers.github import GitHubFilesResolver, GitHubReposResolver

CTX = ResolveContext()


class FakeApi:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, path: str) -> dict[str, Any]:
        self.calls.append(path)
        if path not in self.routes:
            raise RuntimeError(f"404 {path}")
        return self.routes[path]  # type: ignore[no-any-return]


def tree(count: int, *, truncated: bool = False, size: int = 100) -> dict[str, Any]:
    return {
        "truncated": truncated,
        "tree": [{"type": "blob", "size": size} for _ in range(count)]
        + [{"type": "tree"} for _ in range(3)],
    }


# ---------------------------------------------------------------------------- repositories


def test_a_bare_org_resolves_to_every_repository_in_it() -> None:
    """The distinction the resolver exists for: `acme` is not one repository."""
    api = FakeApi({"/orgs/acme": {"public_repos": 40, "total_private_repos": 212}})
    out = GitHubReposResolver(api).resolve("acme", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == 252
    assert out.unit is Unit.REPOSITORIES
    assert out.breakdown == {"private": 212}


def test_a_count_that_cannot_see_private_repositories_is_a_lower_bound() -> None:
    """The wrong-EXACT the live check caught.

    `total_private_repos` is only populated when the token can see inside the account. It came back
    `null` for a real organisation, and the resolver confidently reported EXACT 96 for an org that
    certainly has private repositories. Absent means invisible, and invisible means floor.
    """
    api = FakeApi({"/orgs/acme": {"public_repos": 96, "total_private_repos": None}})
    out = GitHubReposResolver(api).resolve("acme", CTX)

    assert out.magnitude == 96
    assert out.direction is Direction.LOWER_BOUND
    assert out.evidence["private_visible"] is False
    assert may_block(out.direction)
    assert not may_allow(out.direction), "a count missing private repos must not clear a ceiling"


def test_a_count_that_can_see_everything_is_exact() -> None:
    api = FakeApi({"/orgs/acme": {"public_repos": 40, "total_private_repos": 212}})
    out = GitHubReposResolver(api).resolve("acme", CTX)

    assert out.direction is Direction.EXACT
    assert out.evidence["private_visible"] is True


def test_a_personal_account_falls_back_to_the_users_endpoint() -> None:
    """`/orgs/{person}` is a 404. `torvalds` holds twelve repositories and resolved UNRESOLVED
    until a live run found it — a personal account is an entirely ordinary agent target."""
    api = FakeApi({"/users/torvalds": {"public_repos": 12, "type": "User"}})
    out = GitHubReposResolver(api).resolve("torvalds", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == 12
    assert out.evidence["basis"] == "users endpoint"
    assert out.evidence["type"] == "User"
    assert api.calls == ["/orgs/torvalds", "/users/torvalds"], "orgs first — it sees private repos"


def test_an_owner_that_is_neither_reports_both_failures() -> None:
    out = GitHubReposResolver(FakeApi({})).resolve("nobody-at-all", CTX)

    assert out.state is ResolutionState.UNRESOLVED
    assert out.magnitude is None
    assert out.evidence["reason"] == "lookup_failed"
    assert "org_error" in out.evidence


def test_a_named_repository_is_one() -> None:
    api = FakeApi({})
    out = GitHubReposResolver(api).resolve("acme/api", CTX)

    assert out.magnitude == 1
    assert out.direction is Direction.EXACT


def test_naming_one_repository_costs_no_request() -> None:
    """A gate runs on every tool call. Verifying a repository exists would put a round trip in
    front of the cheapest possible answer, and the tool itself fails if it does not exist."""
    api = FakeApi({})
    GitHubReposResolver(api).resolve("acme/api", CTX)
    assert api.calls == []


@pytest.mark.parametrize("target", ["", "   ", "a/b/c", "https://example.com/x/y/z"])
def test_a_target_that_is_not_an_owner_or_repo_is_unresolved(target: str) -> None:
    out = GitHubReposResolver(FakeApi({})).resolve(target, CTX)
    assert out.state is ResolutionState.UNRESOLVED


def test_a_github_url_is_accepted() -> None:
    out = GitHubReposResolver(FakeApi({})).resolve("https://github.com/acme/api", CTX)
    assert out.magnitude == 1


# ---------------------------------------------------------------------------- files


def test_it_counts_the_files_on_the_default_branch() -> None:
    api = FakeApi(
        {
            "/repos/acme/api": {"default_branch": "trunk"},
            "/repos/acme/api/git/trees/trunk?recursive=1": tree(1_400, size=250),
        }
    )
    out = GitHubFilesResolver(api).resolve("acme/api", CTX)

    assert out.magnitude == 1_400, "directories are not files"
    assert out.direction is Direction.EXACT
    assert out.breakdown == {"bytes": 1_400 * 250}
    assert out.evidence["branch"] == "trunk"


def test_a_truncated_tree_is_a_lower_bound() -> None:
    """GitHub caps the tree response and flags it. A capped answer that claimed to be exact would
    make the largest repositories look like the smallest."""
    api = FakeApi(
        {
            "/repos/acme/api": {"default_branch": "main"},
            "/repos/acme/api/git/trees/main?recursive=1": tree(100_000, truncated=True),
        }
    )
    out = GitHubFilesResolver(api).resolve("acme/api", CTX)

    assert out.direction is Direction.LOWER_BOUND
    assert may_block(out.direction)
    assert not may_allow(out.direction), "a repository too large to count must not pass a ceiling"
    assert out.evidence["truncated"] is True


def test_a_bare_org_has_no_tree_to_count() -> None:
    """Sizing an org in files means walking every repository, which is not one request. Declining
    is correct; `github.repos` is the resolver for that target."""
    out = GitHubFilesResolver(FakeApi({})).resolve("acme", CTX)

    assert out.state is ResolutionState.UNRESOLVED
    assert out.evidence["reason"] == "not_a_repository"


def test_a_tree_failure_is_unresolved_not_empty() -> None:
    api = FakeApi({"/repos/acme/api": {"default_branch": "main"}})
    out = GitHubFilesResolver(api).resolve("acme/api", CTX)

    assert out.state is ResolutionState.UNRESOLVED
    assert out.magnitude is None
    assert out.evidence["reason"] == "tree_failed"


def test_an_empty_repository_really_is_zero() -> None:
    """The other half of the pair — a real, exact, safe answer, distinguishable from a failure."""
    api = FakeApi(
        {
            "/repos/acme/api": {"default_branch": "main"},
            "/repos/acme/api/git/trees/main?recursive=1": tree(0),
        }
    )
    out = GitHubFilesResolver(api).resolve("acme/api", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == 0


def test_both_are_registered_under_stable_names() -> None:
    """Policy files outlive releases; a renamed resolver silently stops gating whatever bound it."""
    from neti.resolvers.graph_client import ClientCredential, GraphClient
    from neti.resolvers.registry import resolvers_for_client

    client = GraphClient(ClientCredential(tenant_id="t", client_id="c", client_secret="s"))
    registered = resolvers_for_client(client)
    assert "github.repos" in registered
    assert "github.files" in registered
