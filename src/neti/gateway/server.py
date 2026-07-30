"""A minimal HTTP front for the gateway.

Stdlib `ThreadingHTTPServer` rather than FastAPI/uvicorn, deliberately. The gate is one POST handler
that decodes JSON-RPC, calls `McpGateway.handle`, and writes the response; an ASGI stack would add a
dependency and a worker model without changing a single measurement. When throughput matters this is
the piece to replace, and nothing above it needs to change.

Threading matters for one reason: MCP clients hold a connection open, and a single-threaded server
would serialise every gated call behind the slowest directory read.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from neti.gateway.mcp import McpGateway

__all__ = ["serve"]

SESSION_HEADER = "Mcp-Session-Id"
_MAX_BODY = 8 * 1024 * 1024


def _handler_class(gateway: McpGateway) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "neti/0.1"

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                self._json(413, {"error": {"code": -32600, "message": "request too large"}})
                return

            raw = self.rfile.read(length)
            try:
                message = json.loads(raw)
            except json.JSONDecodeError as exc:
                # A parse error genuinely IS a protocol error, unlike a gate denial.
                self._json(
                    400,
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"parse error: {exc}"},
                    },
                )
                return

            session_id = self.headers.get(SESSION_HEADER)
            try:
                response = gateway.handle(message, session_id)
            except Exception as exc:
                # The gate itself failing is not the agent's fault and must not look like a
                # verdict. Distinguishing the two matters: an operator debugging a blocked call
                # needs to know whether the gate decided or the gate broke.
                self._json(
                    500,
                    {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {"code": -32603, "message": f"neti internal error: {exc}"},
                    },
                )
                return

            if response is None:
                self.send_response(202)  # notification: accepted, nothing to say
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._json(200, response)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            """Silence per-request logging. Decisions go to the record; this would only add noise
            and, worse, put tool arguments in a second place nobody is auditing."""

    return Handler


def serve(gateway: McpGateway, host: str = "127.0.0.1", port: int = 8722) -> None:
    server = ThreadingHTTPServer((host, port), _handler_class(gateway))
    server.serve_forever()
