"""Phase 0, task one: measure Microsoft Graph instead of modelling it.

Every latency number in the plan is modelled. No published p50/p99 for Graph directory reads could
be found, so the synchronous-gate premise rests on an unverified assumption. This script settles it
in an afternoon, against a real tenant, before any architecture is committed.

It answers four questions, in order of how much they can change the design:

1. **Is resolution latency flat in magnitude?** This is the whole argument for Graph over Google. If
   a 41k-member group costs materially more than a 3-member group, `$count` is not behaving as an
   O(1) index read and the caching architecture comes back onto the critical path.
2. **What are the real p50/p99?** If p99 exceeds ~1.5s the synchronous gate needs rework (async
   pre-resolution, or optimistic-hold-with-recall) — decided on data, not on a guess.
3. **Is the external/guest split available in one call?** (Plan risk R2.) If
   `$filter=userType eq 'Guest'` does not work on the cast collection with `$count`, the guest
   breakdown needs full pagination and the POC ships total-count-only.
4. **Does the documented silent-failure mode actually bite?** Graph is documented to ignore
   `?$count=true` when `ConsistencyLevel: eventual` is absent. If it silently returns a member list
   with no count instead of erroring, that is the fail-open bug RESOLVER_CONTRACT.md rule 4 exists
   for, and we want it demonstrated rather than assumed.

Usage:

    export NETI_TENANT_ID=... NETI_CLIENT_ID=... NETI_CLIENT_SECRET=...
    neti measure --group <small-group-id> --group <large-group-id> --repeat 30

Requires an Entra app with `GroupMember.Read.All` (application permission, admin-consented).
Read-only: this script never writes to the directory.
"""

from __future__ import annotations

import contextlib
import os
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"


class MeasureError(RuntimeError):
    pass


@dataclass
class Sample:
    group_id: str
    label: str
    magnitude: int | None
    elapsed_ms: float
    status: int
    ok: bool
    note: str = ""


@dataclass
class GroupResult:
    group_id: str
    display_name: str
    magnitude: int | None
    samples: list[float] = field(default_factory=list)

    @property
    def p50(self) -> float:
        return statistics.median(self.samples) if self.samples else float("nan")

    @property
    def p99(self) -> float:
        if not self.samples:
            return float("nan")
        ordered = sorted(self.samples)
        # nearest-rank, and with n<100 this is the max — stated rather than hidden, because a p99
        # from 30 samples is really "the worst of 30" and should be read that way.
        idx = min(len(ordered) - 1, round(0.99 * (len(ordered) - 1)))
        return ordered[idx]


def acquire_token() -> str:
    """Client-credentials token. Acquired once, outside the measurement, on purpose.

    Token acquisition is 200-700ms and is exactly what the production client caches with background
    refresh. Including it in the samples would measure the wrong thing.
    """
    tenant = _env("NETI_TENANT_ID")
    resp = httpx.post(
        f"{LOGIN}/{tenant}/oauth2/v2.0/token",
        data={
            "client_id": _env("NETI_CLIENT_ID"),
            "client_secret": _env("NETI_CLIENT_SECRET"),
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise MeasureError(f"token request failed {resp.status_code}: {resp.text[:400]}")
    token = resp.json().get("access_token")
    if not token:
        raise MeasureError("token response carried no access_token")
    return str(token)


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise MeasureError(f"{name} is not set")
    return value


def count_transitive_members(
    client: httpx.Client, group_id: str, *, consistency: bool = True
) -> Sample:
    """The call the resolver will make. One request, `text/plain`, an integer.

    Asserts positively, per RESOLVER_CONTRACT.md rule 4: right status, right content type, body
    parses as an integer. Anything else is a failure, not a zero.
    """
    headers = {"ConsistencyLevel": "eventual"} if consistency else {}
    url = f"{GRAPH}/groups/{group_id}/transitiveMembers/$count"
    start = time.perf_counter()
    resp = client.get(url, headers=headers)
    elapsed = (time.perf_counter() - start) * 1000

    ctype = resp.headers.get("content-type", "")
    if resp.status_code != 200:
        return Sample(
            group_id, "count", None, elapsed, resp.status_code, False, note=resp.text[:160]
        )
    if "text/plain" not in ctype:
        return Sample(
            group_id,
            "count",
            None,
            elapsed,
            resp.status_code,
            False,
            note=f"unexpected content-type {ctype!r}",
        )
    body = resp.text.strip()
    if not body.isdigit():
        return Sample(
            group_id,
            "count",
            None,
            elapsed,
            resp.status_code,
            False,
            note=f"body is not an integer: {body[:80]!r}",
        )
    return Sample(group_id, "count", int(body), elapsed, resp.status_code, True)


def probe_guest_filter(client: httpx.Client, group_id: str) -> Sample:
    """Risk R2: is the external/guest share obtainable in one call?

    If this works, `breakdown_bands` on guest counts is viable in the POC. If it does not, the guest
    split needs full pagination and the honest move is to ship total-count-only and say so.
    """
    url = (
        f"{GRAPH}/groups/{group_id}/transitiveMembers/microsoft.graph.user/$count"
        "?$filter=userType eq 'Guest'"
    )
    start = time.perf_counter()
    resp = client.get(url, headers={"ConsistencyLevel": "eventual"})
    elapsed = (time.perf_counter() - start) * 1000
    body = resp.text.strip()
    ok = resp.status_code == 200 and body.isdigit()
    return Sample(
        group_id,
        "guest_filter",
        int(body) if ok else None,
        elapsed,
        resp.status_code,
        ok,
        note="" if ok else body[:200],
    )


def probe_missing_consistency_header(client: httpx.Client, group_id: str) -> Sample:
    """Demonstrate the documented fail-open mode rather than trusting the docs about it.

    Graph is documented to error on `/$count` as a path segment without the header, and to *silently
    ignore* `?$count=true` as a query parameter. Silent is the dangerous one: a client that treats a
    missing count as zero would allow every call.
    """
    url = f"{GRAPH}/groups/{group_id}/transitiveMembers?$count=true&$top=1"
    start = time.perf_counter()
    resp = client.get(url)  # deliberately no ConsistencyLevel
    elapsed = (time.perf_counter() - start) * 1000
    payload: dict[str, Any] = {}
    with contextlib.suppress(ValueError):
        payload = resp.json()
    has_count = "@odata.count" in payload
    return Sample(
        group_id,
        "no_consistency_header",
        payload.get("@odata.count"),
        elapsed,
        resp.status_code,
        ok=False,
        note=(
            "count present despite missing header"
            if has_count
            else f"count silently absent (status {resp.status_code}) — fail-open confirmed"
        ),
    )


def group_display_name(client: httpx.Client, group_id: str) -> str:
    resp = client.get(f"{GRAPH}/groups/{group_id}?$select=displayName,groupTypes,mailEnabled")
    if resp.status_code != 200:
        return "<unknown>"
    data = resp.json()
    kinds = ",".join(data.get("groupTypes") or []) or "security"
    return f"{data.get('displayName', '?')} [{kinds}]"


def measure(group_ids: list[str], repeat: int = 30, timeout_ms: int = 800) -> dict[str, Any]:
    token = acquire_token()
    results: list[GroupResult] = []
    probes: list[Sample] = []

    with httpx.Client(
        http2=True,
        timeout=timeout_ms / 1000,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        for gid in group_ids:
            name = group_display_name(client, gid)
            warm = count_transitive_members(client, gid)  # discard: TLS + connection setup
            res = GroupResult(group_id=gid, display_name=name, magnitude=warm.magnitude)
            for _ in range(repeat):
                sample = count_transitive_members(client, gid)
                if sample.ok:
                    res.samples.append(sample.elapsed_ms)
                    res.magnitude = sample.magnitude
                else:
                    probes.append(sample)
            results.append(res)

        if group_ids:
            probes.append(probe_guest_filter(client, group_ids[-1]))
            probes.append(probe_missing_consistency_header(client, group_ids[-1]))

    return {"groups": results, "probes": probes, "repeat": repeat}


def format_report(out: dict[str, Any]) -> str:
    groups: list[GroupResult] = out["groups"]
    lines: list[str] = []
    lines.append(f"neti measure — Microsoft Graph transitiveMembers/$count, n={out['repeat']}\n")
    lines.append(f"{'group':<44} {'members':>9} {'p50 ms':>8} {'worst ms':>9}")
    lines.append("-" * 74)
    for g in sorted(groups, key=lambda g: g.magnitude or 0):
        lines.append(
            f"{g.display_name[:44]:<44} {g.magnitude if g.magnitude is not None else '?':>9} "
            f"{g.p50:>8.0f} {g.p99:>9.0f}"
        )

    lines.append("")
    measured = [g for g in groups if g.samples and g.magnitude is not None]
    if len(measured) >= 2:
        smallest = min(measured, key=lambda g: g.magnitude or 0)
        largest = max(measured, key=lambda g: g.magnitude or 0)
        ratio_size = (largest.magnitude or 1) / max(smallest.magnitude or 1, 1)
        ratio_time = largest.p50 / max(smallest.p50, 0.001)
        lines.append(f"magnitude ratio {ratio_size:,.0f}x  ->  latency ratio {ratio_time:.2f}x")
        # The claim under test. 1.5x tolerates ordinary variance; anything approaching the magnitude
        # ratio means the endpoint is enumerating, not reading an index.
        if ratio_time <= 1.5:
            lines.append("VERDICT: latency is flat in magnitude — the O(1) premise holds.")
        else:
            lines.append(
                "VERDICT: latency scales with magnitude. The O(1) premise does NOT hold; "
                "revisit the caching architecture before building the synchronous gate."
            )
        worst = max(g.p99 for g in measured)
        lines.append(
            f"worst observed: {worst:.0f} ms — "
            + (
                "within the 800 ms budget."
                if worst <= 800
                else "OVER the 800 ms budget; see plan risk R6 (async pre-resolution)."
            )
        )
    else:
        lines.append("need at least two successfully measured groups to test the flatness claim.")

    lines.append("\nprobes")
    lines.append("-" * 74)
    for p in out["probes"]:
        state = "ok" if p.ok else "--"
        lines.append(f"[{state}] {p.label:<24} status={p.status:<4} {p.note}")
    return "\n".join(lines)
