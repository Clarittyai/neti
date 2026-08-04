"""The one module that opens a socket, and it opens it to *your* provider.

`neti suggest` is bring-your-own-key by construction, not by policy. There is no neti endpoint in
this file, no proxy, no telemetry and nothing to opt out of: the client is constructed with **no
`base_url`**, so the request goes from the operator's process to Anthropic or OpenAI directly, with
the operator's own key, and neti never sees the tool schemas or the answer.

That is not a nicety. The people this product is for are the people who ask what leaves the machine,
and "we forward your internal tool definitions to our server" is a conversation that ends an
evaluation. `tests/property/test_assist_payload.py` asserts this module names no host but the two
below, so the claim is checked rather than promised.

The SDKs are imported *inside* the functions that use them, the same shape `eval/harness/m7.py`
uses, so `pip install neti` pulls in neither and `import neti` never reaches this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

# The only two endpoints this file can reach. Named as constants so a property test can assert
# nothing else appears in the source.
ANTHROPIC_HOST = "api.anthropic.com"
OPENAI_HOST = "api.openai.com"

# Ollama's OpenAI-compatible endpoint. LM Studio is :1234, llama.cpp and vLLM vary, and all of them
# speak the same chat-completions shape — so one client covers every local runner people use.
LOCAL_BASE_URL = "http://localhost:11434/v1"

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_OPENAI_MODEL = "gpt-5"


@dataclass(frozen=True)
class Answer:
    """One response, and what it cost. `usage` is reported so the operator sees what they spent."""

    text: str
    usage: dict[str, int]
    stopped_for: str = ""


class AssistClient(Protocol):
    """Anything that can turn a batch into a response. A protocol so the tests need no network."""

    @property
    def name(self) -> str:
        """The model, for the fragment header and the audit line."""

    @property
    def provider(self) -> str:
        """Where the request went. Always the operator's provider, never neti."""

    def ask(self, system: str, body: str, response_schema: dict[str, Any]) -> Answer: ...


class Refused(RuntimeError):
    """The model declined, or the answer arrived truncated.

    Raised rather than returned so a caller cannot accidentally merge half of one. A truncated
    response looks exactly like a short one, which is `RESOLVER_CONTRACT.md`'s rule about `PARTIAL`
    being unmergeable, applied to a model.
    """


@dataclass
class AnthropicAssist:
    """Anthropic, with the caller's key. Constructed with no base_url, deliberately."""

    model: str = DEFAULT_ANTHROPIC_MODEL
    _provider: str = ANTHROPIC_HOST

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def name(self) -> str:
        return self.model

    def ask(self, system: str, body: str, response_schema: dict[str, Any]) -> Answer:
        import anthropic

        client = anthropic.Anthropic()  # key from ANTHROPIC_API_KEY; no base_url, ever
        message = client.messages.create(
            model=self.model,
            # Generous, because thinking counts against this on current models and a truncated
            # answer is thrown away whole rather than partially merged.
            max_tokens=8000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": body}],
            output_config={"format": {"type": "json_schema", "schema": response_schema}},
        )
        stop = getattr(message, "stop_reason", "") or ""
        if stop in {"refusal", "max_tokens"}:
            raise Refused(stop)
        text = "".join(getattr(block, "text", "") for block in message.content)
        usage = getattr(message, "usage", None)
        return Answer(
            text=text,
            usage={
                "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
                "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
            },
            stopped_for=stop,
        )


@dataclass
class OpenAIAssist:
    """OpenAI, with the caller's key. Same posture, same absence of a base_url."""

    model: str = DEFAULT_OPENAI_MODEL
    _provider: str = OPENAI_HOST

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def name(self) -> str:
        return self.model

    def ask(self, system: str, body: str, response_schema: dict[str, Any]) -> Answer:
        import openai

        client = openai.OpenAI()  # key from OPENAI_API_KEY; no base_url, ever
        response = client.responses.create(
            model=self.model,
            instructions=system,
            input=body,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "claims",
                    "strict": True,
                    "schema": response_schema,
                }
            },
        )
        usage = getattr(response, "usage", None)
        return Answer(
            text=response.output_text,
            usage={
                "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
                "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
            },
        )


@dataclass
class RecordedAssist:
    """A canned answer, for tests and for `--dry-run`. Opens nothing."""

    replies: list[str]
    _name: str = "recorded"
    _provider: str = "none (recorded)"
    asked: list[tuple[str, str]] | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def provider(self) -> str:
        return self._provider

    def ask(self, system: str, body: str, response_schema: dict[str, Any]) -> Answer:
        if self.asked is None:
            self.asked = []
        self.asked.append((system, body))
        if not self.replies:
            raise Refused("no more recorded replies")
        return Answer(text=self.replies.pop(0), usage={"input_tokens": 0, "output_tokens": 0})


@dataclass
class LocalAssist:
    """A model on this machine. Nothing leaves it at all.

    The strongest version of the promise the hosted clients make. `neti suggest` against Ollama, LM
    Studio, llama.cpp or vLLM sends the tool schemas to a process on localhost: no key, no account,
    no third party, and nothing to trust us about. For an operator whose tool definitions are the
    sensitive thing — which is most of the people this product is for — that is the difference
    between an evaluation that proceeds and one that does not.

    **Stdlib only, on purpose.** Every local runner exposes the OpenAI chat-completions shape, and
    it is simple enough that reaching for an SDK would mean `pip install openai` just to talk to a
    process on your own machine. So a local model needs no extra installed: `pip install neti` and
    point it at your runner.

    `base_url` is the one place in this package where a client is aimed somewhere, and it defaults
    to loopback and can only be moved by the operator saying so. `tests/property/
    test_assist_payload.py` asserts the hosted clients still cannot be aimed anywhere at all.
    """

    model: str
    base_url: str = LOCAL_BASE_URL
    # Cold-loading a 30B model from disk is minutes, not seconds, and the first run
    # somebody does is always a cold one.
    timeout_s: float = 900.0

    @property
    def name(self) -> str:
        return self.model

    @property
    def provider(self) -> str:
        return f"{self.base_url} (local)"

    def ask(self, system: str, body: str, response_schema: dict[str, Any]) -> Answer:
        import json
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(
                {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": body},
                    ],
                    # Asked for as a schema where the runner supports it, and a bare JSON object
                    # where it does not. `assist.extract_json` copes with either, and with a model
                    # that wraps the answer in a code fence regardless.
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "claims",
                            "strict": True,
                            "schema": response_schema,
                        },
                    },
                    "temperature": 0,
                    "stream": False,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                data = json.loads(response.read().decode("utf-8"))
        # `TimeoutError` is not a `URLError`, and a large model loading cold takes minutes — so
        # the first real run against a 32B model came back as an unhandled stack trace out of
        # `http.client`. A slow runner is an ordinary thing that must read as one.
        except TimeoutError:
            raise Refused(
                f"no response from {self.base_url} within {self.timeout_s:.0f}s. A large model "
                "loading from cold can take several minutes; raise --timeout, or try a smaller one."
            ) from None
        except (urllib.error.URLError, OSError) as exc:
            raise Refused(
                f"could not reach a local model at {self.base_url}: {exc}. "
                "Is the runner started, and is --model one it has pulled?"
            ) from None

        choices = data.get("choices") or []
        if not choices:
            raise Refused(f"the local model returned no choices: {str(data)[:200]}")
        finish = choices[0].get("finish_reason") or ""
        if finish == "length":
            raise Refused("length")
        usage = data.get("usage") or {}
        return Answer(
            text=choices[0].get("message", {}).get("content", ""),
            usage={
                "input_tokens": int(usage.get("prompt_tokens", 0)),
                "output_tokens": int(usage.get("completion_tokens", 0)),
            },
            stopped_for=finish,
        )


def client_for(provider: str, model: str | None, *, base_url: str | None = None) -> AssistClient:
    """Pick a client, and fail with an installable instruction rather than an ImportError."""
    if provider == "anthropic":
        return AnthropicAssist(model=model or DEFAULT_ANTHROPIC_MODEL)
    if provider == "openai":
        return OpenAIAssist(model=model or DEFAULT_OPENAI_MODEL)
    if provider == "local":
        if not model:
            raise ValueError(
                "--provider local needs --model: a local runner holds several and neti will not "
                "pick one for you. `ollama list` shows what you have."
            )
        return LocalAssist(model=model, base_url=base_url or LOCAL_BASE_URL)
    raise ValueError(f"unknown provider {provider!r}: expected 'anthropic', 'openai' or 'local'")
