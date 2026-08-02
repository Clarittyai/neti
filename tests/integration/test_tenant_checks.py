"""The tenant checks, verified offline before they meet a real tenant.

The point of testing these against the synthetic tenant is that the *failure* paths get exercised.
When an operator runs `neti check` for real they will run it once, and if the R1 branch has a typo
in it the answer they get back is worthless — so the DDG case, the guest-filter failure and the
scaling-latency case are all driven here rather than discovered live.
"""

from __future__ import annotations

import httpx
import pytest

from neti.eval.synthetic import Group, SyntheticTenant, default_tenant
from neti.eval.tenant_checks import Status, discover_targets, format_checks, run_checks
from neti.resolvers.graph_client import ClientCredential, GraphClient

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


def test_a_healthy_tenant_passes_every_check_it_can_answer(tenant: SyntheticTenant) -> None:
    by_id = run(tenant, ["g-solo", "g-eng-all"])
    assert by_id["R1"].status is Status.PASS  # type: ignore[union-attr]
    assert by_id["R2"].status is Status.PASS  # type: ignore[union-attr]
    assert by_id["R2"].data["guests"] == 412  # type: ignore[union-attr,index]


def test_r6_declines_to_answer_against_a_mock_transport(tenant: SyntheticTenant) -> None:
    """R6 asks whether latency is flat in magnitude, and this fixture has no latency.

    Both groups resolve in tens of microseconds in-process, so the ratio between them is scheduling
    jitter — which used to produce an intermittent FAIL on a tenant that does not exist, and would
    have flaked across nine CI matrix jobs. Reporting INFO is both the stable answer and the honest
    one: a PASS derived from a mock transport would be the most misleading line in the report, for
    the same reason `neti check --demo` prints a banner saying it proves nothing about your
    directory.
    """
    r6 = run(tenant, ["g-solo", "g-eng-all"])["R6"]

    assert r6.status is Status.INFO  # type: ignore[union-attr]
    assert r6.data["below_noise_floor"] is True  # type: ignore[union-attr,index]
    assert "real tenant" in r6.detail  # type: ignore[union-attr]


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
    # The remediation is two edits, not one: the shipped policy now binds a resolver that exists
    # only to emit this breakdown, and the validator refuses a band without one.
    changes = r2.changes  # type: ignore[union-attr]
    assert "entra.principals" in changes
    assert "breakdown_bands" in changes


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


def test_it_can_choose_its_own_subjects(tenant: SyntheticTenant) -> None:
    """`neti check` needed group object ids an operator does not have yet.

    R6 asks whether latency is flat in magnitude, which is unanswerable without groups of different
    sizes — so the one diagnostic that unblocks the project sat behind a trip to a portal to find
    ids. Graph will list the groups, and counting them is what this package already does.
    """
    raw = httpx.Client(transport=tenant.transport())
    try:
        ids, how = discover_targets(raw, "synthetic-token")
    finally:
        raw.close()

    # The extremes, not a random pair: two similarly-sized groups cannot answer R6 however many
    # samples are taken.
    assert ids == ["g-solo", "g-eng-all"]
    assert "1 members" in how and "41,203 members" in how


def test_the_selection_is_reported(tenant: SyntheticTenant) -> None:
    """A check that silently picked its own subjects makes a PASS impossible to interpret — the
    reader has to know which groups were measured."""
    raw = httpx.Client(transport=tenant.transport())
    try:
        _, how = discover_targets(raw, "synthetic-token")
    finally:
        raw.close()
    assert how.startswith("auto-selected")


def test_discovery_explains_itself_when_it_cannot_help(tenant: SyntheticTenant) -> None:
    """An empty tenant must produce a sentence an operator can act on, not an empty list."""
    empty = SyntheticTenant(groups={})
    raw = httpx.Client(transport=empty.transport())
    try:
        ids, how = discover_targets(raw, "synthetic-token")
    finally:
        raw.close()

    assert ids == []
    assert "no groups" in how


def test_r2_proves_the_resolver_and_not_only_the_url(tenant: SyntheticTenant) -> None:
    """The raw filter working is necessary and not sufficient.

    `entra.principals_with_guests` is what the shipped policy binds, and it has its own path — two
    resolutions, a min() against the total, a copy that attaches the breakdown. On the single run we
    get against a real tenant, checking only the URL would leave our own code unverified.
    """
    by_id = run(tenant, ["g-solo", "g-eng-all"])
    r2 = by_id["R2"]

    assert r2.status is Status.PASS  # type: ignore[union-attr]
    assert "entra.principals_with_guests emitted" in r2.detail  # type: ignore[union-attr]
    assert r2.data["breakdown"] == {"guest": 412, "internal": 40_791}  # type: ignore[union-attr,index]


# ---------------------------------------------------------------------------- the command itself


def test_the_check_command_runs_end_to_end_under_demo() -> None:
    """`neti check` is the operator's one-shot tenant verification, and until now the *command* had
    never been executed — only `run_checks` had, called directly by the tests above.

    Argument parsing, credential construction, target discovery, report rendering and the exit code
    were an untested seam in front of well-tested logic. `--demo` closes that, so that when someone
    finally has a tenant the only unknown left is the tenant.
    """
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "neti.cli", "check", "--demo", "--repeat", "3"],
        capture_output=True,
        text=True,
    )

    assert out.returncode == 0, out.stderr
    assert "[PASS] R1" in out.stdout
    assert "[PASS] R2" in out.stdout
    assert "STILL MANUAL" in out.stdout


def test_demo_says_it_proves_nothing_about_your_tenant() -> None:
    """The banner is load-bearing. Against a MockTransport R6 reports `worst observed 0 ms` and
    passes — a real-looking latency result measured on nothing at all. A reader who missed that
    this was synthetic would take away the opposite of the truth.
    """
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "neti.cli", "check", "--demo", "--repeat", "3"],
        capture_output=True,
        text=True,
    )

    assert "synthetic tenant, not yours" in out.stdout
    assert "proves nothing about your directory" in out.stdout


def test_without_credentials_it_points_at_demo_rather_than_stopping_dead() -> None:
    """The other half: someone who runs `neti check` with nothing exported should learn both what
    to register *and* that they can see the shape of the answer right now."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if not k.startswith("NETI_")}
    out = subprocess.run(
        [sys.executable, "-m", "neti.cli", "check"], capture_output=True, text=True, env=env
    )

    assert out.returncode == 2
    assert "GroupMember.Read.All" in out.stderr
    assert "neti check --demo" in out.stderr
