"""Seed the MinIO bucket `tests/live/test_s3_live.py` asserts against.

Counts here are asserted exactly, so this file and that one change together.

`big/` deliberately holds more objects than one `ListObjectsV2` page returns. That is the whole
reason for a live S3 tier: the offline suite drives a mock lister that hands back whatever it is
told, so the pagination loop in `S3Lister.list` — the continuation token, the `IsTruncated` flag,
the accumulation across pages — has never run against a server that really paginates.

Idempotent: re-running replaces the objects rather than adding to them.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

BUCKET = os.environ.get("NETI_S3_BUCKET", "neti-live")
ENDPOINT = os.environ.get("NETI_S3_ENDPOINT", "http://127.0.0.1:59000")

SMALL = 12
"""Comfortably inside one page, and inside any ceiling — the ALLOW case."""

BIG = 1_200
"""Past S3's 1,000-key page, so the resolver must follow a continuation token to get this right."""

BODY = b"x" * 16


def main() -> int:
    import boto3

    s3 = boto3.client("s3", endpoint_url=ENDPOINT)

    existing = s3.list_objects_v2(Bucket=BUCKET).get("Contents") or []
    if existing:
        s3.delete_objects(
            Bucket=BUCKET, Delete={"Objects": [{"Key": o["Key"]} for o in existing[:1000]]}
        )
        while True:
            page = s3.list_objects_v2(Bucket=BUCKET).get("Contents") or []
            if not page:
                break
            s3.delete_objects(
                Bucket=BUCKET, Delete={"Objects": [{"Key": o["Key"]} for o in page[:1000]]}
            )

    def put(key: str) -> None:
        s3.put_object(Bucket=BUCKET, Key=key, Body=BODY)

    keys = [f"small/{i:04d}.txt" for i in range(SMALL)]
    keys += [f"big/{i:05d}.txt" for i in range(BIG)]
    with ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(put, keys))

    print(f"seeded s3://{BUCKET}: small/={SMALL} big/={BIG} ({SMALL + BIG} objects)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
