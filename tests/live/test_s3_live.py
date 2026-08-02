"""`storage.objects` against a real S3 API. Opt-in.

    just live-up
    AWS_ACCESS_KEY_ID=netiminio AWS_SECRET_ACCESS_KEY=netiminio123 \
    AWS_DEFAULT_REGION=us-east-1 AWS_ENDPOINT_URL=http://127.0.0.1:59000 \
    NETI_S3_BUCKET=neti-live uv run pytest tests/live/test_s3_live.py -q

MinIO, not AWS. "Local-only" is not a reason to leave this resolver unverified: MinIO speaks real
`ListObjectsV2` with real continuation tokens, which is the part that has never run. Everything
offline drives `ObjectLister` — a Protocol that hands back whatever the test constructed — so the
pagination loop inside `S3Lister.list` was, until this file, dead code as far as the suite was
concerned. `boto3` itself was not even installed in a working checkout.

The resolver is built here the way a deployed one is: `S3Lister()` with no client, letting boto3
resolve credentials and endpoint from the environment. Injecting a pre-built client would test the
loop while skipping the construction, and construction is where an endpoint or a credential chain
goes wrong.

Reads only. `ListObjectsV2` is the sole call this resolver makes.
"""

from __future__ import annotations

import os

import pytest

from neti.core.units import Direction, may_allow, may_block
from neti.core.verdict import ResolutionState
from neti.resolvers.base import ResolveContext
from neti.resolvers.storage import ObjectStoreResolver, S3Lister

BUCKET = os.environ.get("NETI_S3_BUCKET", "")

pytestmark = pytest.mark.skipif(
    not (BUCKET and os.environ.get("AWS_ENDPOINT_URL")),
    reason="live S3 check: `just live-up`, then set AWS_ENDPOINT_URL and NETI_S3_BUCKET",
)

CTX = ResolveContext()

# Must match tests/live/fixtures/seed_minio.py.
SMALL = 12
BIG = 1_200
TOTAL = SMALL + BIG


@pytest.fixture
def objects() -> ObjectStoreResolver:
    return ObjectStoreResolver(S3Lister())


def test_boto3_is_installed() -> None:
    """The reason half of this tier exists.

    `neti[storage]` was in the CI install line and in `just install`, and boto3 was absent from a
    working checkout — because no test needed it. `test_storage_resolver.py` drives a mock lister
    and passes either way.
    """
    import boto3  # noqa: F401


def test_a_prefix_inside_one_page_is_exact(objects: ObjectStoreResolver) -> None:
    out = objects.resolve(f"s3://{BUCKET}/small/", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == SMALL
    assert out.direction is Direction.EXACT
    assert may_allow(out.direction), "a small prefix is the case that must be allowed to pass"
    assert out.breakdown["bytes"] > 0


def test_a_prefix_larger_than_one_page_is_counted_across_pages(
    objects: ObjectStoreResolver,
) -> None:
    """The continuation token, against a server that really issues one.

    `ListObjectsV2` returns at most 1,000 keys, and the fixture holds 1,200 in this prefix. A
    resolver that ignored `IsTruncated` would report exactly 1,000 here — a number that looks
    entirely reasonable, is 200 short, and is wrong in the permissive direction against a ceiling of
    1,100. No mock lister can catch that, because a mock returns whatever the test decided.
    """
    out = objects.resolve(f"s3://{BUCKET}/big/", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == BIG
    assert out.magnitude > 1_000, "the fixture must cross a page boundary or this asserts nothing"
    assert out.direction is Direction.EXACT


def test_the_whole_bucket_is_the_sum_of_its_prefixes(objects: ObjectStoreResolver) -> None:
    out = objects.resolve(f"s3://{BUCKET}", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == TOTAL


def test_the_cap_reports_a_floor_and_never_a_total(objects: ObjectStoreResolver) -> None:
    """A cap presented as a total is a lie in the flattering direction.

    Asserted against a real listing rather than a mock: the resolver stops mid-enumeration, and what
    it returns has to be a `LOWER_BOUND` that can block and can never allow. This is the property
    that makes an aggressive cap survivable — the expensive case and the dangerous case are the same
    case.
    """
    capped = ObjectStoreResolver(S3Lister(), cap=100)
    out = capped.resolve(f"s3://{BUCKET}/big/", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == 100
    assert out.magnitude < BIG, "the cap must actually bite"
    assert out.direction is Direction.LOWER_BOUND
    assert may_block(out.direction)
    assert not may_allow(out.direction), "the prefixes too large to count must not slip through"


def test_a_bucket_that_does_not_exist_is_unresolved_not_zero(
    objects: ObjectStoreResolver,
) -> None:
    """A real `NoSuchBucket` from a real server, not a raise the test arranged.

    An empty prefix and an unreachable one produce the same number if we report one. They are
    opposite situations: the first is the safest possible call, the second is a credential or a
    typo.
    """
    out = objects.resolve("s3://neti-definitely-not-a-real-bucket-xyzzy/", CTX)

    assert out.state is ResolutionState.UNRESOLVED
    assert out.magnitude is None


def test_an_empty_prefix_is_zero_and_says_so(objects: ObjectStoreResolver) -> None:
    """The other half of the pair above: a prefix that really is empty resolves, exactly, to 0.

    Both must hold at once, or the distinction the previous test protects is not being drawn.
    """
    out = objects.resolve(f"s3://{BUCKET}/nothing-here/", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == 0
    assert out.direction is Direction.EXACT


def test_a_target_that_is_not_an_object_store_uri_is_declined(
    objects: ObjectStoreResolver,
) -> None:
    out = objects.resolve("/var/tmp/not-a-uri", CTX)

    assert out.state is ResolutionState.UNRESOLVED
    assert out.evidence["expected"] == "s3://bucket/prefix"
