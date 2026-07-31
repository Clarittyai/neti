"""Telling a human something is waiting.

The console inbox is the source of truth: every request lands there whether or not anything else is
configured, and every decision is made against it. Everything in this module is *notification* —
a nudge toward the inbox, or a channel that can post a decision back into it. That ordering matters,
because a reviewer who approves in Slack and a reviewer who approves in the console must be making
the same decision on the same row, not two decisions that later disagree.

Four channels, and their honest differences:

| channel | how it delivers | what it costs |
|---|---|---|
| console | nothing to send; the inbox *is* the channel | nobody is told, so somebody has to look |
| webhook | POST out, signed; your system posts the decision back | you build the other half |
| Slack | Socket Mode — outbound WebSocket, so no public callback URL, no tunnel | a bot token |
| email | a link to the inbox | slowest, and a forwarded link is an approval |

**A notifier must never be able to stop a call.** Delivery is best-effort and failures are swallowed
after being reported: an SMTP server being down is not a reason to deny an agent, and a channel that
could fail closed would make every integration a new way to take production down. The request is
already safely in the inbox by the time any of this runs — that is what makes swallowing correct
here rather than lazy.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

from neti_cloud.store import ApprovalRow

__all__ = [
    "ConsoleNotifier",
    "EmailNotifier",
    "FanOut",
    "Notifier",
    "NullNotifier",
    "SlackNotifier",
    "WebhookNotifier",
    "summarise",
]

log = logging.getLogger("neti.cloud.notify")


class Notifier(Protocol):
    def notify(self, approval: ApprovalRow) -> None: ...


def summarise(approval: ApprovalRow, console_url: str = "") -> tuple[str, str]:
    """The sentence a reviewer reads, and a link to decide on it.

    Leads with the magnitude, because the magnitude is the entire reason a person is being asked
    rather than a policy engine. "Approve send_email?" is unanswerable; "approve send_email to 500
    recipients, ceiling 50" answers itself.
    """
    evidence = approval.evidence
    tool = evidence.get("tool", "a tool call")
    magnitude = approval.approved_magnitude
    unit = approval.unit or evidence.get("unit") or "items"
    ceiling = evidence.get("ceiling")

    if magnitude is None:
        what = f"{tool} — the target could not be sized"
    else:
        what = f"{tool} resolves to {magnitude:,} {unit}"
        if ceiling is not None:
            what += f", above the declared ceiling of {ceiling:,}"

    link = f"{console_url.rstrip('/')}/approvals/?id={quote(approval.id)}" if console_url else ""
    return what, link


class NullNotifier:
    """The console-only default: the inbox already has it, so there is nothing to send."""

    def notify(self, approval: ApprovalRow) -> None:
        del approval


ConsoleNotifier = NullNotifier


@dataclass
class FanOut:
    """Every configured channel, and one failing does not stop the others."""

    channels: list[Notifier]

    def notify(self, approval: ApprovalRow) -> None:
        for channel in self.channels:
            try:
                channel.notify(approval)
            except Exception:  # see the module docstring: a notifier never fails a call
                log.exception("notifier %s failed for %s", type(channel).__name__, approval.id)


@dataclass
class WebhookNotifier:
    """POST the request to a URL the customer owns.

    The escape hatch: PagerDuty, Teams, ServiceNow and anything else, without us building each one.
    Signed with the org key so the receiver can tell our POST from anyone else's — an unsigned
    webhook is an invitation to have approvals requested by a stranger.
    """

    url: str
    secret: str
    console_url: str = ""
    timeout_s: float = 5.0

    def notify(self, approval: ApprovalRow) -> None:
        import hashlib
        import hmac

        import httpx

        what, link = summarise(approval, self.console_url)
        body = json.dumps(
            {"approval": approval.as_json(), "summary": what, "url": link}, separators=(",", ":")
        ).encode()
        signature = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        httpx.post(
            self.url,
            content=body,
            headers={"Content-Type": "application/json", "X-Neti-Signature": f"sha256={signature}"},
            timeout=self.timeout_s,
        ).raise_for_status()


@dataclass
class SlackNotifier:
    """A message with the magnitude in it, posted to a channel.

    Socket Mode is the reason Slack is demoable at all here: the app connects *outbound* over a
    WebSocket, so there is no public callback URL to expose and no tunnel to run on a laptop. This
    class only posts; the interactive Approve/Deny handler is the Socket Mode listener in
    `neti_cloud.slack_app`, which posts the decision back into the same inbox.
    """

    bot_token: str
    channel: str
    console_url: str = ""
    timeout_s: float = 5.0

    def notify(self, approval: ApprovalRow) -> None:
        import httpx

        what, link = summarise(approval, self.console_url)
        response = httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {self.bot_token}"},
            json={
                "channel": self.channel,
                "text": f"Preflight needs a decision: {what}",
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*Preflight needs a decision*\n{what}"},
                    },
                    {
                        "type": "actions",
                        "block_id": f"neti:{approval.id}",
                        "elements": [
                            _button("Approve", "primary", f"grant:{approval.id}"),
                            _button("Deny", "danger", f"deny:{approval.id}"),
                        ]
                        + ([_link_button("Open the evidence", link)] if link else []),
                    },
                ],
            },
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        if not payload.get("ok"):
            # Slack answers 200 with `ok: false`, so raise_for_status alone would let a failed
            # delivery look successful and leave a request nobody was told about.
            raise RuntimeError(f"slack rejected the message: {payload.get('error')}")


def _button(text: str, style: str, value: str) -> dict[str, Any]:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": text},
        "style": style,
        "value": value,
        "action_id": value.split(":")[0],
    }


def _link_button(text: str, url: str) -> dict[str, Any]:
    return {"type": "button", "text": {"type": "plain_text", "text": text}, "url": url}


@dataclass
class EmailNotifier:
    """A link to the inbox, by SMTP.

    Deliberately a *link* and never approve/deny links that act on their own. A one-click approval
    in an email is an approval anyone the mail is forwarded to can give, and this is the one channel
    where that is most likely to happen quietly. The decision is made in the console, signed in as
    somebody.
    """

    host: str
    sender: str
    recipients: list[str]
    console_url: str = ""
    port: int = 25
    username: str | None = None
    password: str | None = None

    def notify(self, approval: ApprovalRow) -> None:
        import smtplib
        from email.message import EmailMessage

        what, link = summarise(approval, self.console_url)
        message = EmailMessage()
        message["Subject"] = f"Preflight needs a decision: {what[:80]}"
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message.set_content(
            f"An agent's tool call is waiting for a human.\n\n{what}\n\n"
            f"Decide here: {link or '(the neti console)'}\n\n"
            f"Approval {approval.id}, expires {approval.expires_at}.\n"
        )

        with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
            if self.username and self.password:
                smtp.starttls()
                smtp.login(self.username, self.password)
            smtp.send_message(message)
