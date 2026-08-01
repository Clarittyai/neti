"""The object-store resolver, and the one thing that makes its cap safe.

This resolver is the only one here that cannot answer in one request. Counting is O(objects/1000),
so it stops early — and a resolver that stops early is exactly the shape that quietly turns a
too-large target into a small number. The direction rules are what stand between those two, so
that is what most of this file is about.
"""

from __future__ import annotations

import pytest

from neti.core.units import Direction, Unit, may_allow, may_block
from neti.core.verdict import ResolutionState
from neti.resolvers.base import ResolveContext
from neti.resolvers.storage import Listing, ObjectStoreResolver

CTX = ResolveContext()


class FakeLister:
    """A bucket of a known size, counted the way S3 counts."""

    def __init__(self, objects: int, size_each: int = 1024) -> None:
        self.objects = objects
        self.size_each = size_each
        self.calls = 0

    def list(self, bucket: str, prefix: str, cap: int) -> Listing:
        del bucket, prefix
        self.calls += 1
        n = min(self.objects, cap)
        return Listing(objects=n, bytes=n * self.size_each, truncated=self.objects > cap)


class BrokenLister:
    def list(self, bucket: str, prefix: str, cap: int) -> Listing:
        del bucket, prefix, cap
        raise PermissionError("AccessDenied")


def test_a_prefix_it_can_count_is_exact() -> None:
    r = ObjectStoreResolver(FakeLister(1_200))
    out = r.resolve("s3://backups/prod/", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == 1_200
    assert out.direction is Direction.EXACT
    assert out.unit is Unit.OBJECTS
    assert out.breakdown == {"bytes": 1_200 * 1024}


def test_a_prefix_too_large_to_count_is_a_lower_bound() -> None:
    """The cap must not be able to become a small answer."""
    r = ObjectStoreResolver(FakeLister(5_000_000), cap=1_000)
    out = r.resolve("s3://backups/", CTX)

    assert out.magnitude == 1_000
    assert out.direction is Direction.LOWER_BOUND
    assert out.evidence["capped"] is True


def test_a_capped_result_can_block_but_can_never_allow() -> None:
    """The property the cap rests on, asserted against the lattice rather than assumed.

    A prefix so large we gave up counting is the most dangerous prefix there is. If a capped answer
    could pass a ceiling, the resolver would be safest on the targets it understands least.
    """
    out = ObjectStoreResolver(FakeLister(5_000_000), cap=1_000).resolve("s3://b/", CTX)

    assert may_block(out.direction), "measured over a ceiling means the truth is over it too"
    assert not may_allow(out.direction), "measured under a ceiling proves nothing when capped"


def test_a_listing_failure_is_unresolved_not_empty() -> None:
    """An unreachable prefix and an empty one are the same number and opposite situations."""
    out = ObjectStoreResolver(BrokenLister()).resolve("s3://backups/prod/", CTX)

    assert out.state is ResolutionState.UNRESOLVED
    assert out.magnitude is None, "a credential failure must never resolve to 0"
    assert out.evidence["reason"] == "listing_failed"
    assert "AccessDenied" in str(out.evidence["error"])


def test_an_empty_prefix_really_is_zero() -> None:
    """The other half of the pair: a genuinely empty prefix is a real, safe, exact answer."""
    out = ObjectStoreResolver(FakeLister(0)).resolve("s3://backups/nothing-here/", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == 0
    assert out.direction is Direction.EXACT


@pytest.mark.parametrize(
    "target", ["/var/lib/data", "https://example.com/bucket", "backups/prod", ""]
)
def test_a_target_that_is_not_an_object_store_uri_is_unresolved(target: str) -> None:
    """A path handed to the wrong resolver must not be sized by it. This is the misconfiguration
    an operator makes when they bind `storage.objects` to a parameter carrying a local path."""
    out = ObjectStoreResolver(FakeLister(10)).resolve(target, CTX)
    assert out.state is ResolutionState.UNRESOLVED
    assert out.evidence["reason"] == "not_an_object_store_uri"


def test_the_bucket_and_prefix_are_parsed_apart() -> None:
    seen: dict[str, str] = {}

    class Recording:
        def list(self, bucket: str, prefix: str, cap: int) -> Listing:
            del cap
            seen.update(bucket=bucket, prefix=prefix)
            return Listing(objects=1, bytes=1, truncated=False)

    ObjectStoreResolver(Recording()).resolve("s3://prod-backups/2026/04/", CTX)
    assert seen == {"bucket": "prod-backups", "prefix": "2026/04/"}


def test_reachable_max_refuses_to_invent_a_bound() -> None:
    """`neti inventory` asks every resolver what it could reach. For an object store the honest
    answer costs a full bucket enumeration, so it declines rather than guessing."""
    out = ObjectStoreResolver(FakeLister(10)).reachable_max(CTX)
    assert out.state is ResolutionState.UNRESOLVED
    assert out.evidence["reason"] == "no_reachable_hint_declared"


def test_a_declared_reachable_hint_is_an_upper_bound() -> None:
    out = ObjectStoreResolver(FakeLister(10), reachable_hint=2_000_000).reachable_max(CTX)
    assert out.magnitude == 2_000_000
    assert out.direction is Direction.UPPER_BOUND


def test_it_is_registered_under_a_stable_name() -> None:
    """Policy files outlive releases; a renamed resolver silently stops gating whatever bound it."""
    from neti.resolvers.graph_client import ClientCredential, GraphClient
    from neti.resolvers.registry import resolvers_for_client

    client = GraphClient(ClientCredential(tenant_id="t", client_id="c", client_secret="s"))
    assert "storage.objects" in resolvers_for_client(client)


def test_registering_it_does_not_require_boto3_to_be_installed() -> None:
    """The import is deferred to the first call on purpose. If it happened at module scope, a
    missing optional extra would take down every other resolver — including the local ones that
    need no credentials at all — at import time."""
    import subprocess
    import sys

    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.modules['boto3'] = None\n"
            "from neti.resolvers.storage import ObjectStoreResolver, S3Lister\n"
            "print(ObjectStoreResolver(S3Lister()).unit.value)",
        ],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert "objects" in out.stdout
