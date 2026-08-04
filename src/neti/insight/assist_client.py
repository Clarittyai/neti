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


def client_for(provider: str, model: str | None) -> AssistClient:
    """Pick a client, and fail with an installable instruction rather than an ImportError."""
    if provider == "anthropic":
        return AnthropicAssist(model=model or DEFAULT_ANTHROPIC_MODEL)
    if provider == "openai":
        return OpenAIAssist(model=model or DEFAULT_OPENAI_MODEL)
    raise ValueError(f"unknown provider {provider!r}: expected 'anthropic' or 'openai'")
