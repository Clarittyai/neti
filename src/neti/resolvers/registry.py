"""Binding resolver names in a policy file to resolver instances.

The names are stable public API: `entra.principals` in a policy file must keep meaning
`transitiveMembers/$count` forever, because policies outlive releases and a renamed resolver would
silently stop gating whatever bound it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from neti.resolvers.base import Resolver, ResolverError
from neti.resolvers.database import EnvCountRunner, RowsResolver
from neti.resolvers.filesystem import FilesystemResolver
from neti.resolvers.github import GitHubFilesResolver, GitHubReposResolver, HttpGitHubApi
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.graph_entra import (
    EntraAppsResolver,
    EntraGuestsResolver,
    EntraPrincipalsResolver,
    PrincipalsWithGuestBreakdown,
)
from neti.resolvers.storage import ObjectStoreResolver, S3Lister
from neti.resolvers.terraform import TerraformPlanResolver

__all__ = ["PROVIDER_OPTIONS", "build_entra_resolvers", "resolvers_for_client"]


PROVIDER_OPTIONS: dict[str, frozenset[str]] = {
    "fs": frozenset({"root", "cap"}),
    "github": frozenset({"owner"}),
    "db": frozenset({"reachable_rows"}),
    "storage": frozenset({"reachable_objects"}),
    # Declared here so the validator knows it is a real provider. Its values are read by
    # `build_entra_resolvers` from the environment rather than from the file, because a tenant
    # secret does not belong in a policy that gets committed.
    "entra": frozenset({"tenant_id", "auth", "timeout_ms", "consistency"}),
}
"""Every provider block a policy may declare, and every option inside it.

Exhaustive on purpose. `providers:` sat in the policy schema for the whole life of the project and
was read by nothing — a typo, a stale key or a whole block that did nothing would have looked
exactly like working configuration. `Engine` rejects anything not in this table at construction,
which is the same treatment `_check_resolvers_exist` and `_check_breakdown_keys` already give the
other two ways a gate can be born dead.
"""


def _opt(providers: dict[str, dict[str, Any]], provider: str, key: str) -> Any:
    return (providers.get(provider) or {}).get(key)


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(value)


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def resolvers_for_client(
    client: GraphClient, providers: dict[str, dict[str, Any]] | None = None
) -> dict[str, Resolver]:
    """Every resolver, configured from the policy's `providers:` block.

    Configuration matters most for `reachable_max` — the "what could this credential touch in one
    call" number `neti inventory` reports on day one, with no traffic. Without a declared root or
    owner the honest answer is "every file this process can read", which is not a bound, so those
    resolvers decline. Declaring one is what turns the inventory from a table of `?` into a finding.
    """
    providers = providers or {}
    principals = EntraPrincipalsResolver(client)
    guests = EntraGuestsResolver(client)

    fs_root = _opt(providers, "fs", "root")
    fs_cap = _opt(providers, "fs", "cap")
    github_owner = _opt(providers, "github", "owner")

    return {
        "entra.principals": principals,
        "entra.apps": EntraAppsResolver(client),
        "entra.guests": guests,
        # Two requests, not one — which is why it is a separate name. `entra.principals` staying a
        # single O(1) `$count` is the latency claim the whole design rests on, and folding a second
        # round trip into it would quietly double every gated call. The operator opts in.
        "entra.principals_with_guests": PrincipalsWithGuestBreakdown(principals, guests),
        # Neither of these needs a credential — they read local artifacts — so they are always
        # available, including under `--demo`. `fs.paths` is what makes a coding agent gateable at
        # all: without it every tool call a Claude Code or Cursor session makes resolves to nothing
        # and records `allow`.
        "terraform.destroy": TerraformPlanResolver(),
        "fs.paths": FilesystemResolver(
            root=None if fs_root is None else Path(str(fs_root)),
            **({} if fs_cap is None else {"cap": int(fs_cap)}),
        ),
        # Registered unconditionally even though it needs boto3, because the import is deferred to
        # the first call. A policy that binds it without the extra installed fails on that call as
        # UNRESOLVED with the reason in the evidence — which routes through the declared
        # `on_unresolved` — rather than making every other resolver unavailable at import time.
        "storage.objects": ObjectStoreResolver(
            S3Lister(), reachable_hint=_int_or_none(_opt(providers, "storage", "reachable_objects"))
        ),
        # Same lazy shape: connects from NETI_DATABASE_URL on first use, so a policy
        # that does not bind it never needs a database and one that does gets a
        # readable UNRESOLVED instead of a startup crash.
        "db.rows": RowsResolver(
            EnvCountRunner(), reachable_hint=_int_or_none(_opt(providers, "db", "reachable_rows"))
        ),
        # And again for GitHub: the token is read per call from NETI_GITHUB_TOKEN.
        "github.repos": GitHubReposResolver(HttpGitHubApi(), owner=_str_or_none(github_owner)),
        # Ten seconds, not the default 800ms, because the two are not the same class of request.
        # `/orgs/{org}` is a small body; the recursive tree endpoint returns the *whole* tree, and
        # torvalds/linux measured 5.7s against the live API. At 800ms every large repository would
        # time out into UNRESOLVED — safe, but it would mean the resolver only worked on the repos
        # that did not need gating.
        "github.files": GitHubFilesResolver(HttpGitHubApi(timeout_ms=10_000)),
    }


def build_entra_resolvers(
    *, timeout_ms: int = 800, providers: dict[str, dict[str, Any]] | None = None
) -> tuple[dict[str, Resolver], GraphClient]:
    """Construct from the environment. Fails loudly and immediately on a missing credential.

    Deliberately not lazy: a credential problem discovered at startup is a five-minute fix, and the
    same problem discovered on the hot path is every gated call failing closed at once.
    """
    missing = [
        name
        for name in ("NETI_TENANT_ID", "NETI_CLIENT_ID", "NETI_CLIENT_SECRET")
        if not os.environ.get(name)
    ]
    if missing:
        raise ResolverError(
            f"missing credentials: {', '.join(missing)}. "
            "Register an Entra app with GroupMember.Read.All (admin-consented) and export these."
        )

    client = GraphClient(
        ClientCredential(
            tenant_id=os.environ["NETI_TENANT_ID"],
            client_id=os.environ["NETI_CLIENT_ID"],
            client_secret=os.environ["NETI_CLIENT_SECRET"],
        ),
        timeout_ms=timeout_ms,
    )
    return resolvers_for_client(client, providers), client
