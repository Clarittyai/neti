"""`terraform.destroy` against plans a real `terraform` produced. Opt-in.

    NETI_LIVE_TERRAFORM=1 uv run pytest tests/live/test_terraform_live.py -q

No cloud account: the `null` provider makes resources that exist only in state, so a real
`terraform apply` and a real `terraform plan -destroy` can run against nothing at all.

This is the incident on the scorecard. `claude-code-terraform` (2026-02-26) is marked *caught* on
the strength of this resolver, and until now every plan it was shown was a JSON document
written by hand in a test. That is a fixture agreeing with itself. `terraform show -json` has a
`format_version`, an `after_unknown` map whose shape is not obvious, and a distinction between
`delete` and `delete,create` that decides whether a change counts as a destroy or a replace — and
none of it was being read from anything Terraform emitted.

Skipped unless `NETI_LIVE_TERRAFORM=1` and a `terraform` binary is present. The first run downloads
the `null` provider.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from neti.core.units import Direction, may_allow, may_block
from neti.core.verdict import ResolutionState
from neti.resolvers.base import ResolveContext
from neti.resolvers.terraform import TerraformPlanResolver

TERRAFORM = shutil.which("terraform")

pytestmark = pytest.mark.skipif(
    not (TERRAFORM and os.environ.get("NETI_LIVE_TERRAFORM") == "1"),
    reason="live Terraform check: set NETI_LIVE_TERRAFORM=1 with a `terraform` binary on PATH",
)

CTX = ResolveContext()

RESOURCES = 5

MAIN_TF = f"""
terraform {{
  required_providers {{
    null = {{ source = "hashicorp/null", version = "3.2.4" }}
  }}
}}
resource "null_resource" "a" {{ count = {RESOURCES} }}
"""

# One more resource, with an attribute that cannot be known until apply. That unknown is the point:
# it is what makes a real plan resolve as a floor rather than a count.
GROWN_TF = (
    MAIN_TF
    + """
resource "null_resource" "b" {
  triggers = { always = timestamp() }
}
"""
)


def _run(*args: str, cwd: Path) -> None:
    assert TERRAFORM
    proc = subprocess.run(
        [TERRAFORM, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "TF_IN_AUTOMATION": "1"},
    )
    if proc.returncode != 0:
        pytest.fail(f"terraform {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")


def _plan_json(workspace: Path, *plan_args: str) -> str:
    """Produce a plan and return `terraform show -json` output, as an agent's tool would pass it."""
    assert TERRAFORM
    _run("plan", "-no-color", "-input=false", "-out=tf.plan", *plan_args, cwd=workspace)
    shown = subprocess.run(
        [TERRAFORM, "show", "-json", "tf.plan"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return shown.stdout


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real initialised workspace with real state. Built once; every plan below comes from it."""
    root = tmp_path_factory.mktemp("neti-terraform")
    (root / "main.tf").write_text(MAIN_TF, encoding="utf-8")
    _run("init", "-no-color", "-input=false", cwd=root)
    _run("apply", "-auto-approve", "-no-color", "-input=false", cwd=root)
    return root


@pytest.fixture
def plans() -> TerraformPlanResolver:
    return TerraformPlanResolver()


def test_a_real_destroy_plan_counts_every_resource(
    plans: TerraformPlanResolver, workspace: Path
) -> None:
    """`terraform plan -destroy` — the shape of the incident, from the tool that produces it."""
    out = plans.resolve(_plan_json(workspace, "-destroy"), CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == RESOURCES
    assert out.direction is Direction.EXACT
    assert out.breakdown["destroy"] == RESOURCES
    assert out.breakdown["replace"] == 0
    assert out.evidence["counted"] == "destroy + replace (creates and updates are reversible)"
    assert out.provider_snapshot, "a real plan stamps the terraform version; a fixture may not"


def test_a_replace_counts_as_destructive(plans: TerraformPlanResolver, workspace: Path) -> None:
    """`delete,create` is a replacement, and it destroys the old resource just as surely.

    The distinction is two strings in a JSON array, and getting it backwards would classify every
    replacement as a create — reversible, under every ceiling, invisible.
    """
    out = plans.resolve(_plan_json(workspace, "-replace=null_resource.a[0]"), CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.breakdown["replace"] == 1
    assert out.breakdown["destroy"] == 0
    assert out.magnitude == 1, "a replace is counted in the magnitude, not only in the breakdown"


def test_a_plan_that_only_creates_is_zero_and_still_sound(
    plans: TerraformPlanResolver, workspace: Path
) -> None:
    """Creates are reversible, so they are counted and then excluded from the magnitude.

    This plan also carries `after_unknown` — a `timestamp()` trigger is not knowable until apply —
    which is the real reason for this case. Unknown values make a plan's own expansion uncertain, so
    the resolution degrades to a floor: it can still block, and it can no longer allow. Offline that
    branch was reached by hand-writing `after_unknown` into a fixture; here Terraform put it there.
    """
    (workspace / "main.tf").write_text(GROWN_TF, encoding="utf-8")
    try:
        out = plans.resolve(_plan_json(workspace), CTX)
    finally:
        (workspace / "main.tf").write_text(MAIN_TF, encoding="utf-8")

    assert out.state is ResolutionState.RESOLVED
    assert out.breakdown["create"] == 1
    assert out.magnitude == 0, "nothing destructive is proposed"
    assert out.evidence["has_unknown_values"] is True
    assert out.direction is Direction.LOWER_BOUND
    assert may_block(out.direction)
    assert not may_allow(out.direction)


def test_a_no_op_plan_resolves_to_zero(plans: TerraformPlanResolver, workspace: Path) -> None:
    """The common case, and the one that must not be noisy: nothing to do resolves cleanly to 0."""
    out = plans.resolve(_plan_json(workspace), CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == 0
    assert out.direction is Direction.EXACT


def test_the_plan_can_also_be_passed_as_a_path(
    plans: TerraformPlanResolver, workspace: Path
) -> None:
    """Both target shapes, against real output: the JSON inline, or a file this process can open."""
    path = workspace / "destroy.json"
    path.write_text(_plan_json(workspace, "-destroy"), encoding="utf-8")

    out = plans.resolve(str(path), CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == RESOURCES


def test_terraform_state_is_not_a_plan_and_is_declined(
    plans: TerraformPlanResolver, workspace: Path
) -> None:
    """The most dangerous confusion available here, checked against the real artifact.

    `terraform show -json` with no plan file prints *state*, not a plan — same command, same shape
    of JSON, no `resource_changes` key. Reading it as a plan would report that zero resources are
    destroyed, which is the single worst possible wrong answer: it says "this apply is safe".
    """
    assert TERRAFORM
    state = subprocess.run(
        [TERRAFORM, "show", "-json"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    ).stdout
    assert "resource_changes" not in json.loads(state)

    out = plans.resolve(state, CTX)

    assert out.state is ResolutionState.UNRESOLVED
    assert out.magnitude is None
