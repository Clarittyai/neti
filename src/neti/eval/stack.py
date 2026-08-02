"""Every layer an agent can reach, and whether this machine can see it.

`neti demo --here` measured one layer — the filesystem — because that is the only resolver needing
no credential. An agent with a GitHub token, a database URL and an AWS profile reaches four more,
and a gate that reports on one of five while staying quiet about the rest is describing a fraction
of the blast radius as though it were the whole.

So this enumerates the stack rather than the policy. Every layer appears in the output, always, in
one of three states:

- **listening** — a credential is present and the reach was measured
- **dark** — the resolver exists and the credential does not, with the exact variable to export
- **not covered** — no resolver exists for it, which is a limit of this release and is named as one

The third state matters most and is the easiest to omit. A stack table showing only what `neti` can
do would let a reader infer that everything absent is safe, which is precisely backwards: the
layers with no resolver are the ones nothing is watching. `SCOPE.md` already lists them; this puts
them on the same screen as the numbers.

**Detection never authenticates.** Checking for `NETI_GITHUB_TOKEN` is reading an environment
variable, not proving it works — a token that is present and revoked shows as listening here and
fails honestly at the first resolve, through `on_unresolved`. Making the demo verify every
credential would turn a five-second command into a slow one that can fail in five new ways, and the
gate itself does not pre-verify either.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

__all__ = ["LAYERS", "Layer", "StackRow", "State", "survey"]


class State(StrEnum):
    LISTENING = "listening"
    DARK = "dark"
    UNCOVERED = "uncovered"


@dataclass(frozen=True)
class Layer:
    """One place an agent's tool call can land."""

    name: str
    resolver: str
    unit: str
    what: str
    """What one call there can address, in the words an operator would use."""

    needs: str = ""
    """The credential, named exactly as it must be exported. Empty when none is required."""

    declare: str = ""
    """The `providers:` key that gives this layer a bound, when it has one.

    A credential makes a layer *reachable*; it does not say how far. `github.repos` with a valid
    token still cannot answer "how many repositories" until somebody names the organisation they
    mean. Printing the resolver's raw reason code there — `no_owner_declared` — is accurate and
    useless; printing the line to add is the same fact with the remedy attached."""

    covered: bool = True


@dataclass
class StackRow:
    layer: Layer
    state: State
    reach: int | None = None
    bounded: bool = False
    """True when the reach is a floor rather than a total — a capped walk, or a count that could
    not see everything."""

    note: str = ""


LAYERS: tuple[Layer, ...] = (
    Layer(
        name="filesystem",
        resolver="fs.paths",
        unit="objects",
        what="every file under the root an agent is allowed into",
    ),
    Layer(
        name="source control",
        resolver="github.repos",
        unit="repositories",
        what="every repository a token can reach in one call",
        needs="NETI_GITHUB_TOKEN",
        declare="providers.github.owner: <your-org>",
    ),
    Layer(
        name="database",
        resolver="db.rows",
        unit="rows",
        what="the rows one DELETE or UPDATE would take",
        needs="NETI_DATABASE_URL",
        declare="providers.db.reachable_rows: <count>",
    ),
    Layer(
        name="object storage",
        resolver="storage.objects",
        unit="objects",
        what="every object under a bucket prefix",
        needs="AWS_ACCESS_KEY_ID",
        declare="providers.storage.reachable_objects: <count>",
    ),
    Layer(
        name="directory",
        resolver="entra.principals",
        unit="principals",
        what="everyone who loses access when a group is emptied",
        needs="NETI_TENANT_ID",
    ),
    Layer(
        name="infrastructure",
        resolver="terraform.destroy",
        unit="resources",
        what="what a plan would destroy",
    ),
    # ---------------------------------------------------------------- the honest half
    # Named because a table of only what we cover invites the reader to assume the rest is safe.
    # These are the layers where an agent acts and nothing here measures it.
    Layer(
        name="shell",
        resolver="",
        unit="",
        what="what a command would delete — a grammar, not a value (NC-09, NC-10)",
        covered=False,
    ),
    Layer(
        name="messaging",
        resolver="",
        unit="recipients",
        what="Slack, Teams, outbound email outside a directory",
        covered=False,
    ),
    Layer(
        name="SaaS records",
        resolver="",
        unit="records",
        what="CRM objects, tickets, documents in a vendor's own store",
        covered=False,
    ),
)


def _has_github_token() -> bool:
    """Env first, then `gh`, because most developers have the second and neither the first.

    `gh auth token` is a local keychain read, not a network call — so this stays as cheap as
    reading a variable and finds a credential that is genuinely available.
    """
    if os.environ.get("NETI_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        return True
    gh = shutil.which("gh")
    if gh is None:
        return False
    try:
        out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


def _has_aws() -> bool:
    if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
        return True
    return (Path.home() / ".aws" / "credentials").exists()


def _available(layer: Layer) -> bool:
    if not layer.needs:
        return True
    if layer.resolver.startswith("github."):
        return _has_github_token()
    if layer.resolver.startswith("storage."):
        return _has_aws()
    if layer.resolver.startswith("entra."):
        return all(
            os.environ.get(name)
            for name in ("NETI_TENANT_ID", "NETI_CLIENT_ID", "NETI_CLIENT_SECRET")
        )
    return bool(os.environ.get(layer.needs))


def survey(root: Path) -> list[StackRow]:
    """Which layers this machine can see. Reach is filled in by the caller, which owns the policy.

    Split that way on purpose: deciding *whether* a layer is visible is a property of the machine,
    while measuring how far it reaches needs a configured resolver — and the demo already builds
    one. Merging the two here would mean a second construction path for resolvers, which is exactly
    what `eval/scenarios.py` warns against.
    """
    rows: list[StackRow] = []
    for layer in LAYERS:
        if not layer.covered:
            rows.append(StackRow(layer=layer, state=State.UNCOVERED, note="no resolver ships"))
        elif _available(layer):
            rows.append(StackRow(layer=layer, state=State.LISTENING))
        else:
            rows.append(StackRow(layer=layer, state=State.DARK, note=f"export {layer.needs}"))
    return rows
