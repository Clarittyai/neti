"""The tenant checks, verified offline before they meet a real tenant.

The point of testing these against the synthetic tenant is that the *failure* paths get exercised.
When an operator runs `neti check` for real they will run it once, and if the R1 branch has a typo
in it the answer they get back is worthless — so the DDG case, the guest-filter failure and the
scaling-latency case are all driven here rather than discovered live.
"""

from __future__ import annotations

import httpx
import pytest

from neti.eval.tenant_checks import Status, format_checks, run_checks
from neti.resolvers.graph_client import ClientCredential, GraphClient
from tests.integration.synthetic_tenant import Group, SyntheticTenant, default_tenant

CRED = ClientCredential(tenant_id="t", client_id="c", client_secret="s")


@pytest.fixture
def tenant() -> SyntheticTenant:
    return default_tenant()


def run(tenant: SyntheticTenant, targets: list[str], repeat: int = 3) -> dict[str, object]:
    client = GraphClient(CRED, transport=tenant.transport())
    raw = httpx.Client(transport=tenant.transport())
    try:
        results = run_checks(client, raw, "synthetic-token", targets, repeat=repeat)
    finally:
        raw.close()
        client.close()
    return {r.id: r for r in results}


def test_a_healthy_tenant_passes_every_check(tenant: SyntheticTenant) -> None:
    by_id = run(tenant, ["g-solo", "g-eng-all"])
    assert by_id["R1"].status is Status.PASS  # type: ignore[union-attr]
    assert by_id["R2"].status is Status.PASS  # type: ignore[union-attr]
    assert by_id["R6"].status is Status.PASS  # type: ignore[union-attr]
    assert by_id["R2"].data["guests"] == 412  # type: ignore[union-attr,index]


def test_a_dynamic_distribution_group_fails_r1_with_the_right_explanation(
    tenant: SyntheticTenant,
) -> None:
    """The check that can overturn the provider recommendation."""
    by_id = run(tenant, ["g-solo", "g-ddg"])
    r1 = by_id["R1"]
    assert r1.status is Status.FAIL  # type: ignore[union-attr]
    assert "g-ddg" in r1.detail  # type: ignore[union-attr]
    assert "Exchange admin centre" in r1.detail  # type: ignore[union-attr]
    assert "provider recommendation is wrong" in r1.changes  # type: ignore[union-attr]


def test_a_broken_guest_filter_fails_r2_and_says_what_it_costs(
    tenant: SyntheticTenant,
) -> None:
    """Graph rejecting the cast-collection filter is the single most likely real failure here."""
    tenant.fail_when = "microsoft.graph.user"
    tenant.fail_next = [400]
    by_id = run(tenant, ["g-solo", "g-eng-all"])
    r2 = by_id["R2"]
    assert r2.status is Status.FAIL  # type: ignore[union-attr]
    assert "breakdown_bands comes out of the POC" in r2.changes  # type: ignore[union-attr]


def test_latency_scaling_with_magnitude_fails_r6(tenant: SyntheticTenant) -> None:
    """Simulate the Google-shaped failure: cost proportional to blast radius.

    A gate that is slowest exactly when the action is most dangerous is a pathological curve, and
    this is the check that would catch Graph behaving that way despite the documentation.
    """
    import time as _time

    real_handle = tenant._handle

    def slow(request: httpx.Request) -> httpx.Response:
        # 1ms per thousand members, only on the large group's count
        if "g-eng-all" in str(request.url) and "$count" in str(request.url):
            _time.sleep(0.04)
        return real_handle(request)

    client = GraphClient(CRED, transport=httpx.MockTransport(slow))
    raw = httpx.Client(transport=httpx.MockTransport(slow))
    try:
        results = {r.id: r for r in run_checks(client, raw, "t", ["g-solo", "g-eng-all"], repeat=3)}
    finally:
        raw.close()
        client.close()

    r6 = results["R6"]
    assert r6.status is Status.FAIL
    assert "enumerating rather than reading an index" in r6.changes


def test_one_group_skips_the_latency_check_rather_than_faking_it(
    tenant: SyntheticTenant,
) -> None:
    """Flatness is a comparison. With one group there is nothing to compare, so no number."""
    by_id = run(tenant, ["g-solo"])
    assert by_id["R6"].status is Status.INFO  # type: ignore[union-attr]
    assert "at least two groups" in by_id["R6"].detail  # type: ignore[union-attr]


def test_a_failed_lookup_is_never_reported_as_a_pass(tenant: SyntheticTenant) -> None:
    """Regression: an errored probe reported PASS with "resolved, but no counts returned".

    An infrastructure failure reading as a clean result is the exact fail-open shape this project
    exists to avoid, and it is worse in a checklist than anywhere else — the whole value of running
    `neti check` is that a red answer is trustworthy.
    """
    tenant.add(Group("g-mail", "Mailed", transitive_members=7))
    by_id = run(tenant, ["all-staff@example.com"])
    r1 = by_id["R1"]
    assert r1.status is Status.FAIL  # type: ignore[union-attr]
    assert "lookup by mail failed" in r1.detail  # type: ignore[union-attr]
    # and it must not be mistaken for the DDG finding, which means something quite different
    assert "APPLICATION permission" in r1.changes  # type: ignore[union-attr]
    assert "provider recommendation" not in r1.changes  # type: ignore[union-attr]


def test_the_missing_header_probe_confirms_the_fail_open(tenant: SyntheticTenant) -> None:
    by_id = run(tenant, ["g-solo", "g-eng-all"])
    header = by_id["HEADER"]
    assert header.status is Status.INFO  # type: ignore[union-attr]


def test_the_report_names_the_manual_checks_it_cannot_run(tenant: SyntheticTenant) -> None:
    """A checklist that silently drops the items it cannot automate is not a checklist."""
    client = GraphClient(CRED, transport=tenant.transport())
    raw = httpx.Client(transport=tenant.transport())
    try:
        text = format_checks(run_checks(client, raw, "t", ["g-solo", "g-eng-all"], repeat=2))
    finally:
        raw.close()
        client.close()

    assert "STILL MANUAL" in text
    assert "Purview" in text
    assert "MC1024387" in text
    assert "who signs off" in text


def test_failures_are_framed_as_decisions_not_bugs(tenant: SyntheticTenant) -> None:
    client = GraphClient(CRED, transport=tenant.transport())
    raw = httpx.Client(transport=tenant.transport())
    try:
        text = format_checks(run_checks(client, raw, "t", ["g-solo", "g-ddg"], repeat=2))
    finally:
        raw.close()
        client.close()
    assert "design decision arriving on time" in text
