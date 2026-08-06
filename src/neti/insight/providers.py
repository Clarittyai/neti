"""Which model `neti suggest` can reach, and from where.

`neti suggest` is the one thing in this product that talks to a model, and it is bring-your-own-key
by construction: `insight/assist_client.py` builds the hosted clients with **no `base_url`**, so the
request goes from the operator's process to Anthropic or OpenAI directly, with the operator's key,
and neti never sees the tool schemas or the answer. That claim is asserted by a property test, not
promised in a README.

This module is what lets the console say the same thing without weakening it.

**No key is ever read, displayed, stored or accepted here.** The console reports whether a variable
is *set*, never its value, and there is no field to type one into — a key pasted into a browser form
is a key in a process that did not need it, and this product's whole pitch to a security team is
that nothing extra holds their secrets. Export it in the shell that runs `neti`; that is the only
path, and it is the one worth defending.

What the console *can* do is check reachability, which is the part people actually get wrong: a
local runner on the wrong port, a company gateway behind a proxy, a model id that is not loaded.
`probe` answers that by asking the endpoint what models it holds.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

__all__ = ["LOCAL_RUNNERS", "ProviderStatus", "Runner", "probe", "provider_statuses"]


@dataclass(frozen=True)
class Runner:
    """A local model server, with the address it listens on by default and how to start it.

    Every one of these speaks the OpenAI chat-completions shape, which is why one client covers all
    of them and why "your own endpoint" is the same row with a different address rather than a
    separate integration.
    """

    id: str
    label: str
    base_url: str
    start: str


LOCAL_RUNNERS = (
    Runner("ollama", "Ollama", "http://localhost:11434/v1", "ollama serve"),
    Runner("lmstudio", "LM Studio", "http://localhost:1234/v1", "Local Server tab"),
    Runner("vllm", "vLLM", "http://localhost:8000/v1", "vllm serve <model>"),
    Runner("llamacpp", "llama.cpp", "http://localhost:8080/v1", "llama-server"),
)


@dataclass
class ProviderStatus:
    id: str
    label: str
    ready: bool
    """Whether a run would work right now. For the hosted providers that means the environment
    variable is set — **set**, not what it contains, which this never looks at."""

    detail: str
    env: str = ""
    command: str = ""
    installs: str = ""
    """The extra, when there is one. `pip install neti[assist]` for the SDKs; a local runner needs
    nothing at all, which is worth saying out loud."""

    runners: tuple[Runner, ...] = ()
    leaves_machine: bool = True


def provider_statuses() -> list[ProviderStatus]:
    """The three doors, and whether each is open on this machine.

    Presence is read from the environment because that is where the SDKs read it from — reporting
    a key neti had stored somewhere else would be reporting on the wrong thing, and a console that
    said "ready" while `anthropic.Anthropic()` could not find a key would be worse than silent.
    """
    return [
        ProviderStatus(
            id="anthropic",
            label="Anthropic",
            ready=bool(os.environ.get("ANTHROPIC_API_KEY")),
            env="ANTHROPIC_API_KEY",
            detail="your key, your account, straight to api.anthropic.com — neti proxies nothing "
            "and never sees the answer",
            command="neti suggest --provider anthropic",
            installs="pip install 'neti[assist]'",
        ),
        ProviderStatus(
            id="openai",
            label="OpenAI",
            ready=bool(os.environ.get("OPENAI_API_KEY")),
            env="OPENAI_API_KEY",
            detail="the same posture, to api.openai.com. The client is built with no base_url, so "
            "it cannot be aimed anywhere else",
            command="neti suggest --provider openai",
            installs="pip install 'neti[assist]'",
        ),
        ProviderStatus(
            id="local",
            label="A model on this machine",
            # Always available: there is nothing to install and nothing to authenticate. Whether a
            # runner is actually listening is what `probe` answers, per endpoint.
            ready=True,
            detail="anything speaking the OpenAI chat-completions API. Nothing leaves this "
            "machine, and no extra is needed — the local client is stdlib only",
            command="neti suggest --provider local --model <model> --base-url <endpoint>",
            runners=LOCAL_RUNNERS,
            leaves_machine=False,
        ),
    ]


@dataclass
class Probe:
    reachable: bool
    base_url: str
    models: list[str] = field(default_factory=list)
    reason: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "reachable": self.reachable,
            "base_url": self.base_url,
            "models": self.models,
            "reason": self.reason,
        }


def probe(base_url: str, *, timeout_s: float = 3.0) -> Probe:
    """Ask an OpenAI-compatible endpoint what models it holds.

    `GET {base_url}/models` and nothing else. Deliberately not a completion: a probe that ran
    inference would take a cold-loading 30B model minutes to answer and would cost money against a
    hosted gateway, and the question here is only *can neti reach this and what is loaded*.

    Failure is reported with its reason rather than as a bare false. "Connection refused on 11434"
    and "404 at /v1/models" send somebody to completely different places, and a console that
    flattened them to "not reachable" would have thrown away the useful half.
    """
    url = f"{base_url.rstrip('/')}/models"
    if not url.startswith(("http://", "https://")):
        return Probe(False, base_url, reason="an endpoint has to be an http(s) URL")

    # **No Authorization header, ever.** The first version of this attached `OPENAI_API_KEY` on the
    # reasoning that a gateway usually wants one — which would have sent the operator's key to
    # whatever address they typed into a browser field, including a typo and including a hostile
    # host. That is a credential exfiltration path built into a convenience feature.
    #
    # Nothing is lost by removing it. A gateway that requires auth answers `401`, and *reachable,
    # needs auth* is exactly as useful an answer as a model list for the question being asked here.
    request = urllib.request.Request(url, headers={"Accept": "application/json"})

    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            # Reachable, and it wants a key. A different fact from "nothing is listening", and the
            # one that tells somebody their endpoint is right and their shell is missing a variable.
            return Probe(
                False,
                base_url,
                reason=f"reachable, and it requires authentication (HTTP {exc.code}). Export the "
                "key in the shell that runs neti — this check never sends one.",
            )
        return Probe(False, base_url, reason=f"HTTP {exc.code} at {url}")
    except urllib.error.URLError as exc:
        return Probe(False, base_url, reason=f"could not reach {url}: {exc.reason}")
    except (TimeoutError, OSError, ValueError) as exc:
        return Probe(False, base_url, reason=f"could not reach {url}: {exc}")

    rows = payload.get("data") if isinstance(payload, dict) else None
    models = [
        str(row["id"]) for row in rows or [] if isinstance(row, dict) and row.get("id") is not None
    ]
    # Reachable with an empty list is a real and useful answer: the server is up and has nothing
    # loaded, which is a different problem from the server being down.
    return Probe(True, base_url, models=sorted(models))
