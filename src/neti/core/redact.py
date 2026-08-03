"""Keeping secrets out of the audit log.

Every gated call is recorded with its full arguments, and the record is the artefact this product
asks people to keep, verify and hand to an auditor. So an agent calling a tool with a token in one
of its parameters writes that token, in plaintext, into the file most likely to be shared. A
security tool leaking credentials into its own evidence is the worst shape of defect available to
it, and it was there from the first release: `{"api_key": "sk-live-abc123"}` went straight to disk.

**The gated target is never redacted.** That is the one rule everything else bends around. The
target is the evidence — it is what the magnitude was measured from, what `neti report` groups by,
and what a reader checks the verdict against. Redacting it would protect nothing (it is already in
`causes`, which is what the digest and the replay read) and would destroy the record's purpose. If
a parameter holding a credential is *also* the thing being gated, that is an operator's deliberate
choice about their own policy.

**Two independent tests, because either alone is too weak.** A key called `password` should go
whatever it holds, and a value shaped like `ghp_…` should go whatever it is called — agents pass
credentials under names nobody predicted, and predictable names sometimes hold nothing sensitive.

**Redaction is recorded, never silent.** A record says which keys were redacted. A log that quietly
dropped a field would leave a reader unable to tell "the agent sent no key" from "the agent sent a
key and we hid it", and those are very different facts about what happened.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["PLACEHOLDER", "SECRET_KEYS", "redact_args"]

PLACEHOLDER = "[redacted]"
"""Deliberately not empty, not the original length, and ASCII.

An empty string reads as "the agent sent nothing". A run of asterisks matching the original length
hands over the length, which for a fixed-format credential is most of what an attacker needs.

ASCII because the record is NDJSON and `json.dumps` escapes anything else — a first attempt used
guillemets and wrote `\u00abredacted\u00bb` into the file, which is unreadable in a terminal and
un-greppable, in the one artefact whose whole job is being read by a stranger.
"""

SECRET_KEYS = re.compile(
    r"(?:^|_|-)(?:"
    r"secret|password|passwd|pwd|token|api_?key|apikey|access_?key|private_?key|"
    r"credential|credentials|authorization|auth|session_?key|refresh_?token|client_?secret|"
    r"connection_?string|dsn|signature"
    r")(?:$|_|-)",
    re.IGNORECASE,
)
"""Parameter names that hold a credential often enough to be treated as always holding one.

Matched on word boundaries rather than as substrings, so `authorization` matches and `author` does
not — over-redaction is cheap here but it is not free, and a log that hides ordinary fields stops
being read.
"""

_SECRET_VALUES: tuple[re.Pattern[str], ...] = (
    # Provider-issued credentials, by their documented prefixes. These are the ones worth matching
    # exactly: they are unambiguous, and a leaked one is immediately usable by whoever finds it.
    re.compile(r"^gh[pousr]_[A-Za-z0-9]{20,}$"),  # GitHub
    re.compile(r"^github_pat_[A-Za-z0-9_]{20,}$"),
    re.compile(r"^sk-[A-Za-z0-9_-]{16,}$"),  # OpenAI and lookalikes
    re.compile(r"^sk-ant-[A-Za-z0-9_-]{16,}$"),  # Anthropic
    re.compile(r"^xox[baprs]-[A-Za-z0-9-]{10,}$"),  # Slack
    re.compile(r"^AKIA[0-9A-Z]{16}$"),  # AWS access key id
    re.compile(r"^ASIA[0-9A-Z]{16}$"),
    re.compile(r"^eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # The second pass, added after probing this module with the credentials people actually hold.
    # Every one of these went to disk in plaintext when it arrived under a parameter name the key
    # rules do not claim — which is the exact case the value rules exist for, and Stripe is a server
    # in `eval/surveys/catalogue.py`, so it is not a hypothetical agent holding it.
    re.compile(r"^[sr]k_(?:live|test)_[A-Za-z0-9]{16,}$"),  # Stripe secret and restricted keys
    re.compile(r"^AIza[0-9A-Za-z_-]{35}$"),  # Google API key: fixed length, unambiguous
    re.compile(r"^1//[0-9A-Za-z_-]{20,}$"),  # Google OAuth refresh token
    re.compile(r"^pypi-[A-Za-z0-9_-]{16,}$"),  # PyPI
    re.compile(r"^npm_[A-Za-z0-9]{30,}$"),
    re.compile(r"^glpat-[A-Za-z0-9_-]{16,}$"),  # GitLab personal access token
    re.compile(r"^xapp-[0-9]-[A-Za-z0-9-]{10,}$"),  # Slack app-level token
    # A whole `Authorization` header value handed to a tool as a string. The scheme is the tell;
    # anchored so an ordinary sentence beginning with the word cannot match.
    re.compile(r"^Bearer\s+[A-Za-z0-9._~+/-]{20,}=*$"),
    # A URL carrying inline credentials: postgres://user:password@host/db. The password is the
    # point, and connection strings are passed to tools constantly.
    re.compile(r"^[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"),
)


def _looks_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_VALUES)


def redact_args(
    args: dict[str, Any], *, keep: set[str] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Return `(args, redacted_keys)` with credential-shaped values replaced.

    `keep` is the set of top-level keys the policy gates. Those are the evidence and are returned
    untouched — see the module docstring for why that rule wins over every other consideration.
    """
    keep = keep or set()
    redacted: list[str] = []

    def walk(value: Any, path: str, protected: bool) -> Any:
        if isinstance(value, dict):
            return {
                k: walk(v, f"{path}/{k}" if path else f"/{k}", protected) for k, v in value.items()
            }
        if isinstance(value, list):
            return [walk(v, f"{path}/{i}", protected) for i, v in enumerate(value)]
        if protected or not isinstance(value, str) or not value:
            return value
        key = path.rsplit("/", 1)[-1]
        if SECRET_KEYS.search(key) or _looks_secret(value):
            redacted.append(path)
            return PLACEHOLDER
        return value

    def is_gated(key: str) -> bool:
        """True when any gated pointer lands inside this top-level argument.

        Prefix-matched rather than compared exactly, so a policy gating `/payload/path` protects the
        whole `payload` subtree. Matching only the exact pointer would leave the parent unprotected
        and let redaction eat the very value the gate measures.
        """
        return key in keep or any(p == f"/{key}" or p.startswith(f"/{key}/") for p in keep)

    out = {key: walk(value, f"/{key}", protected=is_gated(key)) for key, value in args.items()}
    return out, sorted(redacted)
