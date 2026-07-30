"""Forwarding to the MCP server behind the gate."""

from __future__ import annotations

from typing import Any

import httpx

__all__ = ["HttpUpstream"]

SESSION_HEADER = "Mcp-Session-Id"


class HttpUpstream:
    """Streamable-HTTP MCP server.

    The timeout is separate from, and much larger than, the resolution budget: resolution is our
    latency and belongs to us, while tool execution is the server's and may legitimately take
    minutes. Conflating them would make the gate time out honest long-running tools.
    """

    def __init__(self, url: str, *, timeout_s: float = 120.0) -> None:
        self._url = url
        self._client = httpx.Client(timeout=timeout_s)

    def send(self, message: dict[str, Any], session_id: str | None) -> dict[str, Any] | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if session_id:
            headers[SESSION_HEADER] = session_id

        response = self._client.post(self._url, json=message, headers=headers)

        if "id" not in message:
            return None  # notification
        if not response.content:
            return None
        result: dict[str, Any] = response.json()
        return result

    def close(self) -> None:
        self._client.close()
