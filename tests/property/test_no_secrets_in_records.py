"""Credentials must not reach the audit log, and the evidence must survive redacting them.

This is the worst-shaped defect a security tool can have, and it shipped in the first release: every
gated call recorded its arguments verbatim, so `{"api_key": "sk-live-abc123"}` went straight into
the file this product asks people to keep, verify and hand to an auditor.

Two rules pull against each other and both are load-bearing:

1. **A credential-shaped value never reaches disk.** Not under a suspicious key, not under an
   innocent one, not nested inside a list.
2. **A gated target is never redacted.** It is the evidence — what the magnitude was measured from
   and what a reader checks the verdict against. Redacting it would protect nothing, since `causes`
   carries it anyway, and would make the record useless.

The second rule is why this cannot simply redact everything, and it is the one a well-meaning change
would break first.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from neti.core.redact import PLACEHOLDER, redact_args
from neti.preflight import Preflight
from tests.integration.test_inventory import EXAMPLE

# Real credential shapes. Not invented: these are the formats a leaked one is immediately usable in.
SECRETS = [
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "github_pat_11ABCDEFG0abcdefghijklmnop",
    "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAA",
    "sk-proj-abcdefghijklmnopqrstuvwxyz",
    "xoxb-FIXTURE-SEE-HEAD",
    "AKIAIOSFODNN7EXAMPLE",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEow==\n",
    "postgres://admin:hunter2@db.internal:5432/prod",
    # The second pass. Every one of these reached disk in plaintext under an innocuous parameter
    # name until somebody probed this module with the credentials people actually hold — and
    # Stripe is a server in `eval/surveys/catalogue.py`, so an agent holding one of these is not a
    # hypothetical. The key rules caught them only when the parameter happened to be called
    # something like `api_key`, which is exactly the assumption the value rules exist to remove.
    "sk-live-FIXTURE-SEE-HEAD",
    "rk-live-FIXTURE-SEE-HEAD",
    "sk-test-FIXTURE-SEE-HEAD",
    "AIzaSyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "1//0gAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "pypi-AgEIcHlwaS5vcmcAAAAAAAAAAAAAAAAAAAAA",
    "npm_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "glpat-AAAAAAAAAAAAAAAAAAAA",
    "xapp-1-A0123456789-abcdefghij",
    "Bearer AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
]

# Values that look enough like the patterns above to be worth naming. Over-redaction is cheap but
# it is not free: a log that hides ordinary fields stops being read, and the gated target — the
# evidence the whole record exists for — is an ordinary field.
NOT_SECRETS = [
    "/usr/local/bin/thing",
    "src/**/*.py",
    "g-eng-all",
    "Bearer of bad news",
    "Bearer tokens are handled upstream",
    "AIza",
    "DELETE FROM users WHERE org = 'acme'",
    "s3://backups/prod/",
]


@pytest.mark.parametrize("ordinary", NOT_SECRETS)
def test_an_ordinary_value_is_left_alone(ordinary: str) -> None:
    """The other half of every pattern added above.

    A rule that redacted `s3://backups/prod/` would destroy the evidence rather than protect
    anything — the target is what the magnitude was measured from and what a reader checks the
    verdict against. So each new pattern is anchored and length-bounded, and this is the test that
    keeps it that way.
    """
    from neti.core.redact import redact_args

    safe, redacted = redact_args({"target": ordinary})
    assert safe["target"] == ordinary, f"{ordinary!r} was redacted and should not have been"
    assert not redacted


@pytest.mark.parametrize("secret", SECRETS)
def test_a_credential_shaped_value_goes_whatever_it_is_called(secret: str) -> None:
    """Agents pass credentials under parameter names nobody predicted, so the value has to be
    enough on its own — a key list alone would miss every one of these."""
    out, redacted = redact_args({"harmless_looking_name": secret})

    assert out["harmless_looking_name"] == PLACEHOLDER
    assert redacted == ["/harmless_looking_name"]


@pytest.mark.parametrize(
    "key",
    ["api_key", "apiKey", "password", "client_secret", "auth", "authorization", "refresh_token"],
)
def test_a_credential_shaped_key_goes_whatever_it_holds(key: str) -> None:
    """The other half. A value that does not match a known format is still a secret if it is sitting
    under `password`, and homegrown credentials match no published prefix."""
    out, redacted = redact_args({key: "some-internal-format-nobody-published"})

    assert out[key] == PLACEHOLDER
    assert redacted == [f"/{key}"]


@pytest.mark.parametrize("key", ["author", "authenticity", "keyboard", "tokenizer", "passwordless"])
def test_ordinary_names_are_left_alone(key: str) -> None:
    """Over-redaction is cheap but not free. A log that hides ordinary fields stops being read, and
    an unread log is the same as no log."""
    out, redacted = redact_args({key: "ordinary value"})

    assert out[key] == "ordinary value"
    assert not redacted


def test_the_gated_target_survives_even_when_it_looks_like_a_secret() -> None:
    """The rule everything else bends around.

    A policy gating `/dsn` on `db.rows` is gating a connection string on purpose. Redacting it would
    remove the very thing the verdict was measured from, and protect nothing — `causes` carries the
    target regardless.
    """
    out, redacted = redact_args(
        {"dsn": "postgres://admin:hunter2@db/prod", "other": "postgres://admin:hunter2@db/prod"},
        keep={"/dsn"},
    )

    assert out["dsn"] == "postgres://admin:hunter2@db/prod"
    assert out["other"] == PLACEHOLDER
    assert redacted == ["/other"]


def test_a_nested_gated_pointer_protects_its_whole_subtree() -> None:
    """A policy can gate `/payload/path`. Matching only the exact pointer would leave `payload`
    unprotected and let redaction eat the value the gate measures."""
    out, _ = redact_args({"payload": {"path": "/srv", "token": "ghp_x"}}, keep={"/payload/path"})

    assert out["payload"]["path"] == "/srv"


def test_secrets_nested_in_structures_are_found() -> None:
    """Tool arguments are not flat. A secret one level down is exactly as leaked."""
    out, redacted = redact_args(
        {
            "config": {"db": {"password": "hunter2"}},
            "keys": ["ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"],
        }
    )

    assert out["config"]["db"]["password"] == PLACEHOLDER
    assert out["keys"][0] == PLACEHOLDER
    assert set(redacted) == {"/config/db/password", "/keys/0"}


def test_the_placeholder_leaks_neither_content_nor_length() -> None:
    """An empty string reads as "the agent sent nothing", and a mask matching the original length
    hands over most of what an attacker needs for a fixed-format credential."""
    short, _ = redact_args({"token": "a" * 8})
    long, _ = redact_args({"token": "a" * 400})

    assert short["token"] == long["token"] == PLACEHOLDER


# ---------------------------------------------------------------------------- end to end


@pytest.mark.parametrize("secret", SECRETS)
def test_no_secret_survives_a_real_gated_call(secret: str, tmp_path: Path) -> None:
    """Through the real gate to a real file — the path that actually writes to disk.

    Searching the raw bytes rather than the parsed record, because the question is whether the
    credential is *in the file*, not whether it is in the field somebody remembered to check.
    """
    records = tmp_path / "d.ndjson"
    pf = Preflight.demo(EXAMPLE, mode="observe", records=records)
    pf.check("send_email", {"to": "g-team", "creds": secret, "nested": {"inner": secret}})

    raw = records.read_text()
    assert secret not in raw
    assert secret.splitlines()[0] not in raw, "not even the first line of a multi-line key"
    assert PLACEHOLDER in raw


def test_the_record_says_what_it_redacted(tmp_path: Path) -> None:
    """Silently dropping a field would leave a reader unable to tell "the agent sent no key" from
    "the agent sent a key and we hid it"."""
    records = tmp_path / "d.ndjson"
    pf = Preflight.demo(EXAMPLE, mode="observe", records=records)
    pf.check("send_email", {"to": "g-team", "api_key": "sk-live-abcdefghijklmnop"})

    record = json.loads(records.read_text().splitlines()[0])
    assert record["redacted"] == ["/api_key"]


def test_stripping_the_redaction_marker_breaks_the_chain(tmp_path: Path) -> None:
    """The marker is inside the digest, so a tamperer cannot make a hidden field look absent."""
    from neti.core.record import verify_chain
    from neti.store.jsonl import read_records

    records = tmp_path / "d.ndjson"
    pf = Preflight.demo(EXAMPLE, mode="observe", records=records)
    pf.check("send_email", {"to": "g-team", "api_key": "sk-live-abcdefghijklmnop"})

    doctored = json.loads(records.read_text().splitlines()[0])
    doctored["redacted"] = []
    tampered = tmp_path / "t.ndjson"
    tampered.write_text(json.dumps(doctored) + "\n")

    ok, _ = verify_chain(list(read_records(tampered)))
    assert not ok


def test_the_record_file_is_not_world_readable(tmp_path: Path) -> None:
    """It holds every tool call every agent on this machine has made. On a shared host the default
    umask made that an audit log anyone could read."""
    import sys

    if sys.platform == "win32":
        pytest.skip("POSIX mode bits do not apply")

    records = tmp_path / "d.ndjson"
    Preflight.demo(EXAMPLE, mode="observe", records=records).check("send_email", {"to": "g-team"})

    assert records.stat().st_mode & 0o077 == 0, "group and other must have no access"


# ---------------------------------------------------------------------------- the property


@given(
    key=st.text(min_size=1, max_size=20).filter(lambda s: "/" not in s),
    secret=st.sampled_from(SECRETS),
)
@settings(max_examples=100, deadline=None)
def test_no_key_name_lets_a_known_credential_through(key: str, secret: str) -> None:
    """Whatever an agent calls it, a value in a published credential format does not reach disk."""
    out, _ = redact_args({key: secret})
    assert out[key] == PLACEHOLDER
