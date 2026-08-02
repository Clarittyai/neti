"""How many objects, and how many bytes, is this prefix.

Object stores are where agents are handed bulk-delete verbs: an `aws s3 rm --recursive` tool, an
MCP S3 server, a backup-rotation script an agent was told to tidy up. `s3://bucket/prefix` is a
string that looks the same whether it addresses nine objects or nine million, which is the exact
shape this product exists for.

**It is not O(1), and that difference is the point.** Every other resolver here answers in one
request because the provider offers a counting endpoint: Graph has `$count`, a Terraform plan is a
file. Object stores have no such thing at prefix granularity — S3's `ListObjectsV2` returns a
thousand keys a page, so counting two million objects is two thousand requests. There is no way to
make that cheap and no honest way to pretend otherwise.

So it is capped hard and low, and past the cap the answer is a `LOWER_BOUND`: we enumerated at least
this many and stopped. The decision procedure already knows what that means — sound to block on,
never sound to allow on — so a prefix too large to count is a prefix that cannot quietly pass. The
expensive case and the dangerous case are the same case, which is the only reason a cap this
aggressive is safe.

**It does not close the `pocketos-railway` scorecard miss**, which is what it was picked up to do.
That was a Railway *block volume*, deleted by ID through Railway's own API — there is no prefix to
enumerate, and closing it needs a Railway resolver mapping volume ID to size. Its proximate cause
was also an unscoped credential, which is an authorization failure upstream of any magnitude gate
(`SCOPE.md` NC-04). Sizing a call is not the same as deciding the caller should have been able to
make it. The scorecard stays at three of seven and its note says both of those things.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Protocol
from urllib.parse import urlparse

from neti.core.types import Resolution
from neti.core.units import Direction, Unit
from neti.resolvers.base import ResolveContext

__all__ = ["DEFAULT_CAP", "Listing", "ObjectLister", "ObjectStoreResolver", "S3Lister"]

DEFAULT_CAP = 50_000
"""Objects to enumerate before reporting a lower bound.

Fifty pages of `ListObjectsV2`, so roughly a second against S3. Low on purpose: a prefix with more
than fifty thousand objects is past any ceiling an operator would declare, so the exact figure has
stopped mattering — and the cheap answer and the safe answer point the same way.
"""


@dataclass(frozen=True)
class Listing:
    objects: int
    bytes: int
    truncated: bool
    """True when the cap was hit. The magnitude is then a floor, not a count."""


class ObjectLister(Protocol):
    """The provider seam, so the resolver is testable without a cloud account.

    Same shape as `GraphClient`'s injectable transport, and for the same reason: a resolver whose
    only test is against a live bucket is a resolver nobody runs the tests for.
    """

    def list(self, bucket: str, prefix: str, cap: int) -> Listing: ...


@dataclass
class S3Lister:
    """`ListObjectsV2`, paginated, stopping at the cap. Needs `neti[storage]` for boto3."""

    client: object | None = None

    def list(self, bucket: str, prefix: str, cap: int) -> Listing:
        client = self.client
        if client is None:
            try:
                import boto3
            except ImportError as exc:  # surfaced as UNRESOLVED, so say what to install
                raise RuntimeError("storage.objects needs boto3; install `neti[storage]`") from exc

            client = boto3.client("s3")

        objects = 0
        total = 0
        token: str | None = None
        while True:
            kw = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            page = client.list_objects_v2(**kw)  # type: ignore[attr-defined]
            for item in page.get("Contents") or []:
                objects += 1
                total += int(item.get("Size") or 0)
                if objects >= cap:
                    return Listing(objects=objects, bytes=total, truncated=True)
            if not page.get("IsTruncated"):
                return Listing(objects=objects, bytes=total, truncated=False)
            token = page.get("NextContinuationToken")
            if not token:
                return Listing(objects=objects, bytes=total, truncated=False)


@dataclass
class ObjectStoreResolver:
    """Sizes an `s3://bucket/prefix` target in objects, with bytes as a breakdown.

    Objects is the magnitude rather than bytes because a ceiling in objects is the one an operator
    can reason about — "no call may touch more than 10,000 files" is a sentence somebody will
    defend, and "no call may touch more than 3.7e10 bytes" is not. Bytes rides along as a breakdown
    so a policy can band on it too: ten objects can be a larger loss than ten thousand.
    """

    lister: ObjectLister
    cap: int = DEFAULT_CAP
    reachable_hint: int | None = None
    """What `neti inventory` reports. Left `None` unless an operator declares it, because "every
    object this credential can reach" needs a bucket-wide count nobody should pay for on a
    report."""

    unit: ClassVar[Unit] = Unit.OBJECTS
    breakdown_keys: ClassVar[frozenset[str]] = frozenset({"bytes"})

    def resolve(self, target: str, ctx: ResolveContext) -> Resolution:
        del ctx
        parsed = urlparse(target)
        if parsed.scheme not in ("s3", "gs") or not parsed.netloc:
            return Resolution.unresolved(
                self.unit,
                reason="not_an_object_store_uri",
                evidence={"target": target[:200], "expected": "s3://bucket/prefix"},
            )

        prefix = parsed.path.lstrip("/")
        try:
            listing = self.lister.list(parsed.netloc, prefix, self.cap)
        except Exception as exc:  # every provider failure is UNRESOLVED, never 0
            # An empty prefix and an unreachable one look identical if we report a number. One of
            # them is the safest possible call and the other is a credential problem.
            return Resolution.unresolved(
                self.unit,
                reason="listing_failed",
                evidence={"error": str(exc)[:200], "bucket": parsed.netloc, "prefix": prefix},
            )

        return Resolution.resolved(
            self.unit,
            listing.objects,
            direction=Direction.LOWER_BOUND if listing.truncated else Direction.EXACT,
            resolved_at=datetime.now(UTC),
            # Not `strong`. A list is a point-in-time view of a store being written to, and S3's own
            # listings are read-after-write consistent for new keys but not for a whole enumeration.
            consistency="eventual",
            breakdown={"bytes": listing.bytes},
            evidence={
                "bucket": parsed.netloc,
                "prefix": prefix,
                "capped": listing.truncated,
                "cap": self.cap,
            },
        )

    def reachable_max(self, ctx: ResolveContext) -> Resolution:
        del ctx
        if self.reachable_hint is None:
            return Resolution.unresolved(
                self.unit,
                reason="no_reachable_hint_declared",
                evidence={
                    "hint": (
                        "counting a whole bucket costs a full enumeration; declare the figure if "
                        "you want it on the inventory rather than paying for it on every report"
                    )
                },
            )
        return Resolution.resolved(
            self.unit,
            self.reachable_hint,
            direction=Direction.UPPER_BOUND,
            resolved_at=datetime.now(UTC),
            consistency="eventual",
            evidence={"basis": "operator-declared"},
        )
