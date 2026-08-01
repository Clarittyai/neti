"""The tenant-side checks, as one command.

Four facts about a real Microsoft 365 tenant currently gate the whole project, and every one of them
is unanswerable offline. They are collected here so that answering them costs one command rather
than four separate investigations, and so the logic is already tested against the synthetic tenant
before it ever meets a real one.

Each check reports what its answer *changes*, because a red result here is not a bug — it is a
design decision arriving on time:

- **R1** — is the target an Entra group, or an Exchange dynamic distribution group? DDGs are
  computed by Exchange and never synced to Entra, so Graph 404s on them everywhere. If they
  dominate the estate, the provider recommendation is wrong.
- **R2** — does the guest filter work on the cast collection with `$count`? If not,
  `breakdown_bands` comes out of the POC and external share degrades to total-count-only.
- **R6** — is resolution latency flat in magnitude, and inside the 800ms budget? Every latency
  figure in the plan is modelled and no published Graph p50/p99 exists. If p99 is above ~1.5s the
  synchronous design needs rework.
- **HEADER** — does omitting `ConsistencyLevel` fail open, as documented? Confirms the hazard that
  RESOLVER_CONTRACT.md rule 4 exists to catch, on the actual provider rather than on trust.

The Purview question cannot be automated — it is a look at a rule builder in a portal — so it is
printed as a reminder rather than silently dropped.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from enum import StrEnum
from statistics import median
from typing import Any

import httpx

from neti.resolvers.base import ResolveContext
from neti.resolvers.graph_client import GRAPH, GraphClient
from neti.resolvers.graph_entra import (
    EntraGuestsResolver,
    EntraPrincipalsResolver,
    PrincipalsWithGuestBreakdown,
)

__all__ = ["CheckResult", "Status", "format_checks", "run_checks"]


class Status(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    INFO = "INFO"


@dataclass
class CheckResult:
    id: str
    title: str
    status: Status
    detail: str
    changes: str = ""
    """What a failure here changes about the plan. Empty when nothing downstream depends on it."""

    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class GroupProbe:
    target: str
    resolved_id: str | None = None
    display_name: str | None = None
    kind: str | None = None
    members: int | None = None
    guests: int | None = None
    error: str | None = None
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def p50(self) -> float:
        return median(self.latencies_ms) if self.latencies_ms else float("nan")

    @property
    def worst(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else float("nan")


def _lookup_group(client: GraphClient, raw: httpx.Client, token: str, target: str) -> GroupProbe:
    """Accept an object id or a mail address, because operators think in addresses."""
    probe = GroupProbe(target=target)
    headers = {"Authorization": f"Bearer {token}", "ConsistencyLevel": "eventual"}

    if "@" in target:
        response = raw.get(
            f"{GRAPH}/groups",
            params={"$filter": f"mail eq '{target}'", "$select": "id,displayName,groupTypes"},
            headers=headers,
        )
        if response.status_code != 200:
            probe.error = f"lookup by mail failed: {response.status_code} {response.text[:120]}"
            return probe
        values = response.json().get("value") or []
        if not values:
            # The interesting negative. Graph cannot distinguish a deleted group from an Exchange
            # dynamic distribution group, and saying so is more useful than guessing.
            probe.error = "no Entra group has this mail address"
            probe.kind = "not-in-entra"
            return probe
        probe.resolved_id = values[0]["id"]
        probe.display_name = values[0].get("displayName")
        probe.kind = ",".join(values[0].get("groupTypes") or []) or "security"
        return probe

    response = raw.get(
        f"{GRAPH}/groups/{target}",
        params={"$select": "id,displayName,groupTypes,mailEnabled"},
        headers=headers,
    )
    if response.status_code == 404:
        probe.error = "not an Entra directory object"
        probe.kind = "not-in-entra"
        return probe
    if response.status_code != 200:
        probe.error = f"{response.status_code} {response.text[:120]}"
        return probe
    body = response.json()
    probe.resolved_id = body["id"]
    probe.display_name = body.get("displayName")
    probe.kind = ",".join(body.get("groupTypes") or []) or "security"
    return probe


def run_checks(
    client: GraphClient,
    raw: httpx.Client,
    token: str,
    targets: list[str],
    *,
    repeat: int = 20,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    probes: list[GroupProbe] = []

    # ---------------------------------------------------------------- R1
    for target in targets:
        probe = _lookup_group(client, raw, token, target)
        if probe.resolved_id:
            outcome = client.count(f"/groups/{probe.resolved_id}/transitiveMembers/$count")
            probe.members = outcome.magnitude if outcome.ok else None
            if not outcome.ok:
                probe.error = outcome.reason
        probes.append(probe)

    invisible = [p for p in probes if p.kind == "not-in-entra"]
    # A probe that errored for any other reason — a bad credential, a throttle, a network fault —
    # is *not* a pass. An earlier version reported "resolved, but no counts returned" as PASS,
    # which is the same fail-open shape this whole project exists to avoid: an infrastructure
    # failure reading as a clean result.
    broken = [p for p in probes if p.error and p not in invisible]

    if invisible:
        results.append(
            CheckResult(
                id="R1",
                title="target is resolvable by Graph",
                status=Status.FAIL,
                detail=(
                    f"{len(invisible)} of {len(probes)} target(s) are not Entra directory objects: "
                    + ", ".join(p.target for p in invisible)
                    + ". Graph cannot distinguish a deleted group from an Exchange dynamic "
                    "distribution group — check the Group type column in the Exchange admin centre."
                ),
                changes=(
                    "If DDGs dominate the estate the Microsoft provider recommendation is wrong. "
                    "Exchange PowerShell is not viable on a synchronous path, so the resolver "
                    "strategy needs revisiting before more is built on it."
                ),
                data={"invisible": [p.target for p in invisible]},
            )
        )
    elif broken:
        results.append(
            CheckResult(
                id="R1",
                title="target is resolvable by Graph",
                status=Status.FAIL,
                detail="; ".join(f"{p.target}: {p.error}" for p in broken),
                changes=(
                    "Nothing about the design yet — this is a credential, permission or network "
                    "problem, and the tenant questions stay unanswered until it is fixed. Check "
                    "that GroupMember.Read.All is granted as an APPLICATION permission and that "
                    "admin consent was given."
                ),
            )
        )
    else:
        results.append(
            CheckResult(
                id="R1",
                title="target is resolvable by Graph",
                status=Status.PASS,
                detail="; ".join(
                    f"{p.display_name or p.target} [{p.kind}] = {p.members:,} members"
                    for p in probes
                    if p.members is not None
                ),
            )
        )

    sized = [p for p in probes if p.resolved_id and p.members is not None]

    # ---------------------------------------------------------------- R2
    if not sized:
        results.append(
            CheckResult(
                id="R2",
                title="guest breakdown is O(1)",
                status=Status.INFO,
                detail="skipped — no target resolved",
            )
        )
    else:
        probe = sized[-1]
        url = f"{GRAPH}/groups/{probe.resolved_id}/transitiveMembers/microsoft.graph.user/$count"
        response = raw.get(
            url,
            params={"$filter": "userType eq 'Guest'"},
            headers={"Authorization": f"Bearer {token}", "ConsistencyLevel": "eventual"},
        )
        body = response.text.strip()
        if response.status_code == 200 and body.isdigit():
            probe.guests = int(body)

            # The raw request working is necessary and not sufficient.
            # `entra.principals_with_guests` is what the shipped policy binds, and it has its own
            # path: two resolutions, a min() against the total, and a copy that attaches the
            # breakdown. Checking only the URL leaves our own code unverified on the one run we get.
            composite = PrincipalsWithGuestBreakdown(
                EntraPrincipalsResolver(client), EntraGuestsResolver(client)
            )
            resolved = composite.resolve(str(probe.resolved_id), ResolveContext())
            emitted = dict(resolved.breakdown)

            if "guest" in emitted:
                results.append(
                    CheckResult(
                        id="R2",
                        title="guest breakdown is O(1)",
                        status=Status.PASS,
                        detail=(
                            f"{probe.guests:,} guests of {probe.members:,} in one request; "
                            f"entra.principals_with_guests emitted {emitted}"
                        ),
                        data={"guests": probe.guests, "breakdown": emitted},
                    )
                )
            else:
                results.append(
                    CheckResult(
                        id="R2",
                        title="guest breakdown is O(1)",
                        status=Status.FAIL,
                        detail=(
                            "the raw filter works but entra.principals_with_guests emitted no "
                            f"breakdown: {resolved.evidence.get('guest_breakdown', 'no reason')}"
                        ),
                        changes=(
                            "a bug in this package rather than in Graph — the provider answered. "
                            "Fix the resolver before drawing any conclusion about the tenant."
                        ),
                    )
                )
        else:
            results.append(
                CheckResult(
                    id="R2",
                    title="guest breakdown is O(1)",
                    status=Status.FAIL,
                    detail=f"{response.status_code}: {body[:200]}",
                    changes=(
                        "the external-share story degrades to total-count-only. Remediation is two "
                        "edits, not one: in examples/entra.yaml point send_email/to back at "
                        "`entra.principals` AND delete its `breakdown_bands`. Removing only the "
                        "band leaves the two-request resolver paying for a breakdown nobody reads; "
                        "removing only the resolver is refused at startup, because a band no "
                        "resolver emits can never fire."
                    ),
                )
            )

    # ---------------------------------------------------------------- R6
    if len(sized) < 2:
        results.append(
            CheckResult(
                id="R6",
                title="latency is flat in magnitude and inside budget",
                status=Status.INFO,
                detail=(
                    "skipped — pass at least two groups of very different sizes; the claim under "
                    "test is that a 40,000-member group costs the same as a 3-member one"
                ),
            )
        )
    else:
        for probe in sized:
            client.count(f"/groups/{probe.resolved_id}/transitiveMembers/$count")  # warm
            for _ in range(repeat):
                start = time.perf_counter()
                client.count(f"/groups/{probe.resolved_id}/transitiveMembers/$count")
                probe.latencies_ms.append((time.perf_counter() - start) * 1000)

        smallest = min(sized, key=lambda p: p.members or 0)
        largest = max(sized, key=lambda p: p.members or 0)
        size_ratio = (largest.members or 1) / max(smallest.members or 1, 1)
        time_ratio = largest.p50 / max(smallest.p50, 1e-6)
        worst = max(p.worst for p in sized)

        flat = time_ratio <= 1.5
        in_budget = worst <= 800
        status = Status.PASS if (flat and in_budget) else Status.FAIL
        detail = (
            f"{size_ratio:,.0f}x more members cost {time_ratio:.2f}x the time; "
            f"worst observed {worst:.0f} ms"
        )
        changes = ""
        if not flat:
            changes = (
                "Latency scales with magnitude, so the endpoint is enumerating rather than reading "
                "an index — the gate would be slowest exactly when the action is most dangerous, "
                "and the caching architecture comes back onto the critical path. "
            )
        if not in_budget:
            changes += (
                "Above the 800ms budget the synchronous design needs rework: async pre-resolution "
                "or optimistic-hold-with-recall, both of which reintroduce staleness."
            )
        results.append(
            CheckResult(
                id="R6",
                title="latency is flat in magnitude and inside budget",
                status=status,
                detail=detail,
                changes=changes,
                data={
                    p.target: {"members": p.members, "p50_ms": p.p50, "worst_ms": p.worst}
                    for p in sized
                },
            )
        )

    # ---------------------------------------------------------------- HEADER
    if sized:
        probe = sized[0]
        response = raw.get(
            f"{GRAPH}/groups/{probe.resolved_id}/transitiveMembers",
            params={"$count": "true", "$top": "1"},
            headers={"Authorization": f"Bearer {token}"},  # deliberately no ConsistencyLevel
        )
        # The body here is untrusted in shape as well as content: this endpoint can return a JSON
        # object, a JSON array, or a bare integer depending on how Graph interprets the request.
        # Assuming a dict is how the first version of this crashed.
        payload: Any = None
        with contextlib.suppress(ValueError):
            payload = response.json()
        has_count = isinstance(payload, dict) and "@odata.count" in payload
        silently_ignored = response.status_code == 200 and not has_count
        results.append(
            CheckResult(
                id="HEADER",
                title="omitting ConsistencyLevel fails open, as documented",
                status=Status.INFO,
                detail=(
                    "confirmed: 200 OK with no count in the body — a client that read a missing "
                    "count as zero would allow every call"
                    if silently_ignored
                    else f"status {response.status_code}; count present: {has_count}"
                ),
                changes=(
                    ""
                    if not silently_ignored
                    else "No change — this is the hazard RESOLVER_CONTRACT.md rule 4 already "
                    "guards against, now confirmed on the real provider."
                ),
            )
        )

    return results


def format_checks(results: list[CheckResult]) -> str:
    out = ["neti tenant checks", "=" * 72, ""]
    for r in results:
        out.append(f"[{r.status.value:<4}] {r.id:<7} {r.title}")
        out.append(f"         {r.detail}")
        if r.changes:
            out.append(f"         WHAT THIS CHANGES: {r.changes}")
        out.append("")

    failures = [r for r in results if r.status is Status.FAIL]
    out.append("-" * 72)
    if failures:
        out.append(f"{len(failures)} check(s) failed: {', '.join(r.id for r in failures)}")
        out.append("A failure here is a design decision arriving on time, not a bug.")
    else:
        out.append("All automated checks passed.")
    out.append("")
    out.append("STILL MANUAL — cannot be automated:")
    out.append(
        "  Purview: does the DLP rule builder offer 'Unique recipients greater than'? "
        "Microsoft announced GA (MC1024387) but it is absent from the current docs. It changes "
        "the pitch, not the code — but assume an informed buyer believes it shipped."
    )
    out.append(
        "  Demand:  ask ~8 people how many times in 90 days they killed a run for touching more "
        "than expected, what ceiling they would write, and who signs off. If six cannot name a "
        "number and an owner, the configuration surface does not exist."
    )
    return "\n".join(out)


def discover_targets(
    raw: httpx.Client, token: str, *, sample: int = 40, want: int = 2
) -> tuple[list[str], str]:
    """Find groups spanning a range of sizes, so `neti check` needs no arguments.

    R6 asks whether resolution latency is flat in magnitude, which is unanswerable without groups of
    *different* sizes — so the command required an operator to go and find object ids in a portal
    before they could run the one diagnostic that unblocks the project. That is a real barrier in
    front of the single most important pending task, and it is removable: Graph will list the groups
    and the counts are the thing we already know how to fetch.

    Returns `(ids, how)`. `how` is displayed, because a check that silently chose its own subjects
    would make a PASS impossible to interpret — the reader has to know which groups were measured.
    """
    listing = raw.get(
        f"{GRAPH}/groups",
        params={"$select": "id,displayName", "$top": str(sample)},
        headers={"Authorization": f"Bearer {token}", "ConsistencyLevel": "eventual"},
    )
    if listing.status_code != 200:
        return [], f"could not list groups ({listing.status_code})"

    candidates = [g for g in (listing.json().get("value") or []) if g.get("id")]
    if not candidates:
        return [], "the tenant has no groups this credential can see"

    sized: list[tuple[int, str]] = []
    for group in candidates:
        counted = raw.get(
            f"{GRAPH}/groups/{group['id']}/transitiveMembers/$count",
            headers={"Authorization": f"Bearer {token}", "ConsistencyLevel": "eventual"},
        )
        body = counted.text.strip()
        if counted.status_code == 200 and body.isdigit():
            sized.append((int(body), str(group["id"])))

    if not sized:
        return [], "no group could be counted — check the GroupMember.Read.All grant"

    sized.sort()
    # The extremes, not a random pair. The claim under test is that magnitude does not affect
    # latency, and two similarly-sized groups cannot answer it however many samples are taken.
    picked = [sized[0]] if len(sized) == 1 else [sized[0], sized[-1]]
    picked = picked[:want]
    how = "auto-selected " + ", ".join(f"{n:,} members" for n, _ in picked)
    return [gid for _, gid in picked], how
