"""The GitHub resolvers against the real api.github.com. Opt-in.

    NETI_GITHUB_TOKEN=$(gh auth token) uv run pytest tests/live -q

Skipped without a token, so CI and a fresh clone stay offline. This exists because running it once
by hand found three defects that fifteen offline tests did not, and a verification nobody can repeat
is a verification that decays:

- **`/orgs/{owner}` 404s for a person.** `torvalds` holds twelve repositories and came back
  UNRESOLVED. A personal account is an entirely ordinary target for an agent with a token.
- **A wrong EXACT.** `total_private_repos` is `null` when the token cannot see inside the account,
  so a real organisation resolved to EXACT 96 while its private repositories were invisible. Absent
  means invisible, and invisible means the count is a floor.
- **`github.files` needs seconds, not milliseconds.** torvalds/linux measured 5.7s. At the 800ms
  default every large repository would have timed out — safe, but the resolver would only have
  worked on the repositories that did not need gating.

Every call here is a read. The assertions are deliberately loose about *values* — a public
repository's file count changes — and strict about *shape*, direction and soundness, which is what
the offline suite cannot check.
"""

from __future__ import annotations

import os

import pytest

from neti.core.units import Direction, may_allow, may_block
from neti.core.verdict import ResolutionState
from neti.resolvers.base import ResolveContext
from neti.resolvers.github import GitHubFilesResolver, GitHubReposResolver, HttpGitHubApi

pytestmark = pytest.mark.skipif(
    not (os.environ.get("NETI_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")),
    reason="live GitHub check: set NETI_GITHUB_TOKEN (e.g. `gh auth token`)",
)

CTX = ResolveContext()


@pytest.fixture
def repos() -> GitHubReposResolver:
    return GitHubReposResolver(HttpGitHubApi(timeout_ms=10_000))


@pytest.fixture
def files() -> GitHubFilesResolver:
    return GitHubFilesResolver(HttpGitHubApi(timeout_ms=20_000))


def test_a_real_organisation_resolves(repos: GitHubReposResolver) -> None:
    out = repos.resolve("anthropics", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude is not None
    assert out.magnitude > 10, "a real org has repositories"
    assert out.evidence["basis"] == "orgs endpoint"


def test_a_real_person_resolves_via_the_users_endpoint(repos: GitHubReposResolver) -> None:
    """The defect this file was written for. `/orgs/torvalds` is a 404."""
    out = repos.resolve("torvalds", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude is not None
    assert out.magnitude > 0
    assert out.evidence["basis"] == "users endpoint"
    assert out.evidence["type"] == "User"


def test_an_account_whose_private_repos_we_cannot_see_is_a_lower_bound(
    repos: GitHubReposResolver,
) -> None:
    """The wrong-EXACT. Unless the token is inside the account, the count is a floor."""
    out = repos.resolve("anthropics", CTX)

    if out.evidence["private_visible"]:
        pytest.skip("this token can see inside the org, so EXACT is correct here")
    assert out.direction is Direction.LOWER_BOUND
    assert not may_allow(out.direction)


def test_a_nonexistent_owner_is_unresolved_not_zero(repos: GitHubReposResolver) -> None:
    out = repos.resolve("neti-definitely-not-a-real-owner-xyzzy", CTX)

    assert out.state is ResolutionState.UNRESOLVED
    assert out.magnitude is None


def test_a_real_repository_file_count_is_exact(files: GitHubFilesResolver) -> None:
    out = files.resolve("anthropics/anthropic-sdk-python", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude is not None
    assert out.magnitude > 100
    assert out.direction is Direction.EXACT
    assert out.evidence["truncated"] is False
    assert out.breakdown["bytes"] > 0


def test_a_repository_too_large_to_enumerate_truncates(files: GitHubFilesResolver) -> None:
    """GitHub's own `truncated` flag, on a real repository that really does trip it.

    The offline suite asserts what happens *given* truncation. Only this asserts that truncation
    happens at all, and that the endpoint still reports it the way the resolver assumes.
    """
    out = files.resolve("torvalds/linux", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.evidence["truncated"] is True
    assert out.direction is Direction.LOWER_BOUND
    assert may_block(out.direction)
    assert not may_allow(out.direction), "the largest repos must not be the ones that slip through"
