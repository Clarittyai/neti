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


# This used to read "no client is constructed with a base_url anywhere", which was the right rule
# until `--provider local` arrived: pointing at a model on your own machine is the *strongest*
# version of the promise, not a breach of it. Deleting the test would have been the easy move and
# the wrong one, so it got narrower instead.
#
# The rule that actually matters: a client that talks to somebody else's server must not be
# aimable. The hosted clients therefore still cannot take a base_url in any spelling, and the local
# one defaults to loopback and moves only when the operator says so.
HOSTED_CLIENTS = {"AnthropicAssist", "OpenAIAssist"}


def test_a_hosted_client_can_never_be_aimed_somewhere_else() -> None:
    """A base_url on these two is how a proxy gets introduced with nothing else changing.

    Checked on the syntax inside each class rather than by string search, so any spelling fails.
    """
    tree = ast.parse(CLIENT_SOURCE)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name not in HOSTED_CLIENTS:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and any(k.arg == "base_url" for k in inner.keywords):
                offenders.append(f"{node.name} line {inner.lineno}")
    assert not offenders, f"a hosted client is being pointed somewhere: {offenders}"


def test_the_local_client_defaults_to_this_machine() -> None:
    """`--provider local` must not become a way to send schemas to a stranger by default.

    The operator can point it anywhere deliberately — that is the feature — but the default has to
    be loopback, so the only way schemas leave the machine is somebody typing an address.
    """
    from urllib.parse import urlparse

    host = urlparse(assist_client.LOCAL_BASE_URL).hostname
    assert host in {"localhost", "127.0.0.1", "::1"}, f"the local default points at {host}"
    assert assist_client.LocalAssist(model="m").base_url == assist_client.LOCAL_BASE_URL


def test_a_local_model_needs_no_sdk_installed() -> None:
    """Reaching for an SDK to talk to a process on your own machine is a dependency for nothing.

    `pip install neti` and point it at Ollama: no key, no account, no extra. Asserted by importing
    the module with neither provider SDK importable.
    """
    import subprocess
    import sys

    probe = (
        "import sys;"
        "sys.modules['anthropic'] = None; sys.modules['openai'] = None;"
        "from neti.insight.assist_client import LocalAssist;"
        "print(LocalAssist(model='m').provider)"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert "local" in out.stdout


# `assist.py` is the pure half and must reach nothing at all. `assist_client.py` is the half whose
# job is to open a socket, so the rule there is not "no HTTP client" — it is *where* it can reach.
#
# The first version banned `urllib` from both files, which was right until `--provider local`
# arrived and needed exactly that to reach a process on the operator's own machine. Narrowed
# rather than deleted: what is asserted now is that every address hardcoded in the module is a
# provider endpoint or loopback, and everything else has to be typed by the operator.
NETWORKING = {"httpx", "requests", "urllib", "http", "socket", "aiohttp", "anthropic", "openai"}


def test_the_pure_module_reaches_nothing() -> None:
    """Batching, prompting, parsing and rendering need no network and must not acquire one."""
    imported = _imports(ASSIST_SOURCE)
    assert not (imported & NETWORKING), (
        f"assist.py imports something network-capable: {sorted(imported & NETWORKING)}"
    )


def test_every_address_the_client_hardcodes_is_a_provider_or_this_machine() -> None:
    """The client may open sockets. It may not decide where to, beyond these three."""
    urls = set(re.findall(r"https?://[^\s\"'\)]+", CLIENT_SOURCE))
    from urllib.parse import urlparse

    for url in urls:
        host = urlparse(url).hostname or ""
        assert host in {"localhost", "127.0.0.1", "::1"} | ALLOWED_HOSTS, (
            f"assist_client.py hardcodes {url}, which is neither a provider nor this machine"
        )


def _imports(source: str) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


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


def test_the_question_asks_for_exactly_the_parameters_it_sends() -> None:
    """The enumeration and the payload cannot disagree, because one is built from the other.

    A model that answers for a parameter the tool does not have loses that answer *and* the
    parameters it was actually asked about: `parse` drops the unknown name and nothing fills the
    gap. Measured against `tests/corpus/`, one such guess on `move_file` cost three results.
    """
    batch = _candidates()
    asked = assist.question(batch)
    for candidate in batch:
        assert f"{candidate.tool}/{candidate.parameter}" in asked
    assert f"exactly {len(batch)} claim(s)" in asked


def test_the_question_sends_no_name_the_payload_does_not_already_carry() -> None:
    """The enumeration is a restatement, not a second channel.

    It would be easy to make the ask clearer by adding a type, a default, a sample value or a path —
    each of which is something the operator never agreed to send. So every identifier in the tail,
    meaning every token carrying a `/`, `.`, `_` or `-`, has to appear in the payload above it.
    """
    import json
    import re

    batch = _candidates()
    inside = json.dumps(assist.payload(batch))
    tail = assist.question(batch).split("\n\n", 1)[1]

    identifiers = [w for w in re.findall(r"[\w./-]+", tail) if re.search(r"[/._-]", w)]
    assert identifiers, "the enumeration named nothing at all"
    for word in identifiers:
        # `tool/parameter` is a composite key, so each half is checked against the payload rather
        # than the joined form, which by construction appears nowhere in the JSON.
        for half in word.split("/"):
            assert half in inside, f"{half!r} is in the ask but not in the payload"


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
