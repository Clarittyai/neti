"""Invariant: `neti suggest` sends the operator's schemas to the operator's provider only.

Bring-your-own-key is the whole shape of this feature, and it is the kind of claim that is easy to
write in a README and easy to erode in a refactor. Somebody adds a "share your suggestions to
improve the catalogue" flag, or a retry through a proxy, or a metrics ping, and the promise is gone
while the sentence about it stays.

So it is asserted here, against the source, three ways: no host but the operator's provider appears
in the module that opens sockets, no client is ever constructed with a `base_url`, and the payload
carries exactly four keys with the policy, the ceilings, the records and the machine's paths nowhere
among them.

The audience for this product is the audience that asks what leaves the machine. "We forward your
internal tool definitions to our server" is a conversation that ends an evaluation, and no amount of
prose is worth as much as a test somebody can run.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from neti.insight import assist, assist_client

CLIENT_SOURCE = Path(assist_client.__file__).read_text(encoding="utf-8")
ASSIST_SOURCE = Path(assist.__file__).read_text(encoding="utf-8")

ALLOWED_HOSTS = {assist_client.ANTHROPIC_HOST, assist_client.OPENAI_HOST}


def test_the_only_hosts_named_are_the_two_providers() -> None:
    """Any other hostname in this file is a route the operator did not agree to."""
    found = set(re.findall(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)+\b", CLIENT_SOURCE))
    hosts = {
        h
        for h in found
        if h.endswith((".com", ".net", ".org", ".io", ".ai", ".dev", ".sh"))
        and not h.endswith(".py")
    }
    assert hosts <= ALLOWED_HOSTS, (
        f"this module names a host it should not: {sorted(hosts - ALLOWED_HOSTS)}"
    )


def test_no_client_is_constructed_with_a_base_url() -> None:
    """A base_url is how a proxy gets introduced without anything else changing.

    Checked on the syntax rather than by string search, so `base_url = something` in any spelling
    fails rather than only the literal keyword.
    """
    tree = ast.parse(CLIENT_SOURCE)
    offenders = [
        f"line {node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "base_url"
    ]
    assert not offenders, f"a client is being pointed somewhere: {offenders}"


# Only the two provider SDKs may open a socket. An HTTP client imported here is a route to
# somewhere the operator did not choose, and the first version of this test looked for the *word*
# "telemetry" in the source — which failed on a docstring promising there is none. A test that reads
# prose rather than code measures how the thing was described.
SOCKET_CAPABLE = {"anthropic", "openai"}
NEVER_IMPORTED = {"httpx", "requests", "urllib", "http", "socket", "aiohttp"}


def test_only_the_provider_sdks_can_open_a_socket() -> None:
    """The alternative is a second route out of the machine that nothing here would notice."""
    for name, source in (("assist.py", ASSIST_SOURCE), ("assist_client.py", CLIENT_SOURCE)):
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        assert not (imported & NEVER_IMPORTED), (
            f"{name} imports an HTTP client of its own: {sorted(imported & NEVER_IMPORTED)}"
        )
        if name == "assist.py":
            assert not (imported & SOCKET_CAPABLE), "the pure module must reach no provider at all"


# ---------------------------------------------------------------------------- the payload


def _candidates() -> tuple[assist.Candidate, ...]:
    from neti.insight.discover import DeclinedParam, ToolSpec

    return assist.eligible(
        [
            ToolSpec(
                name="acme__wipe",
                description="Delete rows matching a filter",
                params=("table", "filter", "confirm"),
                gated=(),
                destructive=True,
                declined=(
                    DeclinedParam(param="table", why="?"),
                    DeclinedParam(param="confirm", why="?"),
                ),
            )
        ]
    )


def test_the_payload_has_exactly_four_keys() -> None:
    """Asserted as equality, not as a subset. A subset test cannot see a fifth key arriving."""
    for entry in assist.payload(_candidates()):
        assert set(entry) == {"tool", "description", "siblings", "parameters"}


@pytest.mark.parametrize(
    "forbidden",
    ["neti.yaml", "ceiling", "bands", "decisions.ndjson", "NETI_", "argv", "command", "env"],
)
def test_the_payload_carries_nothing_about_this_machine(forbidden: str) -> None:
    """The policy, the ceilings, the records, the credentials, the server command line.

    None of it is needed to answer "does this parameter name a set", and all of it is somebody's
    internal configuration.
    """
    import json

    body = json.dumps(assist.payload(_candidates()))
    assert forbidden not in body


def test_the_system_prompt_never_asks_for_a_number() -> None:
    """The model is on the far side of every quantity in the system, and this is where that starts.

    Direction is declared by the resolver and units belong to the parameter's role. A prompt that
    asked for a magnitude or a ceiling would be the first step towards a model's arithmetic reaching
    a verdict, whatever the rendering did afterwards.
    """
    # Whitespace-normalised: the prompt is hard-wrapped, and a test that breaks when a sentence
    # is rewrapped is a test about formatting.
    prompt = " ".join(assist.SYSTEM.split())
    assert "You are not asked for a number, a ceiling, a direction, or a risk judgement" in prompt

    # The guarantee that actually holds, rather than a phrase search over the prose: the response
    # shape has nowhere to put a quantity. A model cannot return a magnitude, a direction, a unit or
    # a ceiling because the schema rejects any property but these four, and `additionalProperties`
    # is false. The first version of this grepped for "how many" and failed on the sentence
    # explaining what neti does, which is a test reading the description instead of the contract.
    item = assist.schema()["properties"]["claims"]["items"]
    assert set(item["properties"]) == {"tool", "parameter", "resolver", "why"}
    assert item["additionalProperties"] is False
    for numeric in ("magnitude", "count", "ceiling", "direction", "unit", "verdict"):
        assert numeric not in item["properties"]


def test_importing_neti_never_reaches_the_client() -> None:
    """The SDKs are optional and the socket module must not be on the import path of the package."""
    import subprocess
    import sys

    probe = "import sys, neti; print('assist_client' in ' '.join(sys.modules))"
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, check=True
    )
    assert out.stdout.strip() == "False", (
        "`import neti` must not load the module that opens sockets"
    )
