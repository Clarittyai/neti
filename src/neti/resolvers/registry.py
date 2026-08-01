"""Binding resolver names in a policy file to resolver instances.

The names are stable public API: `entra.principals` in a policy file must keep meaning
`transitiveMembers/$count` forever, because policies outlive releases and a renamed resolver would
silently stop gating whatever bound it.
"""

from __future__ import annotations

import os

from neti.resolvers.base import Resolver, ResolverError
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.graph_entra import (
    EntraAppsResolver,
    EntraGuestsResolver,
    EntraPrincipalsResolver,
    PrincipalsWithGuestBreakdown,
)
from neti.resolvers.terraform import TerraformPlanResolver

__all__ = ["build_entra_resolvers", "resolvers_for_client"]


def resolvers_for_client(client: GraphClient) -> dict[str, Resolver]:
    principals = EntraPrincipalsResolver(client)
    guests = EntraGuestsResolver(client)
    return {
        "entra.principals": principals,
        "entra.apps": EntraAppsResolver(client),
        "entra.guests": guests,
        # Two requests, not one — which is why it is a separate name. `entra.principals` staying a
        # single O(1) `$count` is the latency claim the whole design rests on, and folding a second
        # round trip into it would quietly double every gated call. The operator opts in.
        "entra.principals_with_guests": PrincipalsWithGuestBreakdown(principals, guests),
        # Needs no credential at all — it reads a local plan artifact — so it is always available.
        "terraform.destroy": TerraformPlanResolver(),
    }


def build_entra_resolvers(*, timeout_ms: int = 800) -> tuple[dict[str, Resolver], GraphClient]:
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
    return resolvers_for_client(client), client
