"""The stdio transport — the one almost everybody actually uses.

An MCP server in Claude Code, Claude Desktop or Cursor is a `command` the client *launches*, and it
speaks newline-delimited JSON-RPC over the child's stdin and stdout. Remote HTTP servers exist, but
the local subprocess is overwhelmingly the common case, so a gate that only speaks HTTP is a gate
nobody can install without also re-hosting their tools.

Installing this is a one-line edit to the client's config: whatever `command` and `args` used to
launch the server become arguments to `neti gate --stdio --`, and the client is now talking to the
gate. Nothing about the server changes and nothing about the agent changes — the whole claim.

Three things are load-bearing here and all three are easy to get wrong:

**stdout carries the protocol and nothing else.** One stray `print`, one banner, one warning, and
the client's parser desynchronises and the session dies. Every human-readable byte this module emits
goes to stderr, and the child's stderr is pumped to ours so its logs survive.

**The child talks back unprompted.** A stdio MCP server is not a request/response service: it emits
notifications (`notifications/message`, progress) and can issue server→client *requests*
(`sampling/createMessage`, `roots/list`). Anything arriving from the child that is not a response to
a request we are waiting on is forwarded verbatim. Demultiplexing on the JSON-RPC `id` is what makes
that possible, and a naive "write, then read one line" upstream would corrupt the stream the first
time a server logged anything.

**Requests are pipelined.** Clients issue concurrent `tools/call`s whenever a model emits parallel
tool uses. Handling inbound messages on a pool keeps one slow tool from stalling the session; a
single lock around stdout keeps the frames from interleaving.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import IO, Any

from neti.gateway.mcp import McpGateway

__all__ = ["StdioUpstream", "serve_stdio"]

_WORKERS = 8


def _drop(message: dict[str, Any]) -> None:
    """Default for server-initiated messages, replaced by `serve_stdio` with a real writer."""
    del message


def _write(stream: IO[str], lock: threading.Lock, message: dict[str, Any]) -> None:
    line = json.dumps(message, separators=(",", ":"))
    with lock:
        stream.write(line + "\n")
        stream.flush()


class StdioUpstream:
    """An MCP server launched as a child process.

    `send` blocks on the matching response rather than on the next line, because the next line is
    very often something else entirely.
    """

    on_unsolicited: Callable[[dict[str, Any]], None]
    """Where server-initiated messages go. `serve_stdio` rebinds this to its own locked writer, so
    that every byte reaching the client passes through exactly one lock."""

    def __init__(
        self,
        argv: list[str],
        *,
        on_unsolicited: Callable[[dict[str, Any]], None] | None = None,
        timeout_s: float = 120.0,
        echo_stderr: bool = True,
    ) -> None:
        if not argv:
            raise ValueError("no command given to launch")

        self._timeout_s = timeout_s
        self._echo_stderr = echo_stderr
        self.stderr_tail: list[str] = []
        """The child's last few stderr lines, kept so a failure can be explained with the server's
        own words rather than with a timeout and nothing else."""

        self.on_unsolicited = on_unsolicited or _drop
        self._pending: dict[str, Future[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()

        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding="utf-8",
        )
        assert self._proc.stdin and self._proc.stdout and self._proc.stderr

        self._reader = threading.Thread(target=self._pump_stdout, daemon=True)
        self._reader.start()
        self._errs = threading.Thread(target=self._pump_stderr, daemon=True)
        self._errs.start()

    # ------------------------------------------------------------------ Upstream

    def send(self, message: dict[str, Any], session_id: str | None) -> dict[str, Any] | None:
        del session_id  # stdio has one session by construction: this process pair.
        stdin = self._proc.stdin
        if stdin is None or self._proc.poll() is not None:
            raise RuntimeError("the MCP server behind the gate has exited")

        if "id" not in message:
            _write(stdin, self._write_lock, message)  # notification: nothing comes back
            return None

        key = json.dumps(message["id"])
        future: Future[dict[str, Any]] = Future()
        with self._pending_lock:
            self._pending[key] = future

        _write(stdin, self._write_lock, message)
        try:
            return future.result(timeout=self._timeout_s)
        finally:
            with self._pending_lock:
                self._pending.pop(key, None)

    def close(self) -> None:
        """Stop the child and give back its file descriptors.

        Closing stdin first is what lets a well-behaved server exit on its own; `terminate` is the
        fallback for one that does not. The pipes are closed explicitly rather than left to the
        garbage collector — a gate that relaunches servers would otherwise leak three descriptors
        per restart, and the leak only shows up as a mysterious ceiling much later.
        """
        with contextlib.suppress(OSError):
            if self._proc.stdin is not None:
                self._proc.stdin.close()

        if self._proc.poll() is None:
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=5)

        # The reader threads own these; joining first keeps a close from racing a read.
        for thread in (self._reader, self._errs):
            thread.join(timeout=2)
        for pipe in (self._proc.stdout, self._proc.stderr):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()

    # ------------------------------------------------------------------ internals

    def _pump_stdout(self) -> None:
        stdout = self._proc.stdout
        assert stdout is not None
        for line in stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                # Not our protocol. Some servers print to stdout despite the spec; passing it to
                # the client would break the client, so it goes where diagnostics belong.
                print(f"neti: non-JSON on server stdout: {line}", file=sys.stderr)
                continue
            if not isinstance(message, dict):
                continue

            key = json.dumps(message.get("id")) if "id" in message else None
            future = None
            if key is not None:
                with self._pending_lock:
                    future = self._pending.get(key)

            # A response we are waiting on, or a server-initiated message that belongs to the
            # client. There is no third case, and conflating them is how sampling breaks.
            if future is not None and ("result" in message or "error" in message):
                future.set_result(message)
            else:
                self.on_unsolicited(message)

        self._fail_pending(RuntimeError("the MCP server behind the gate closed its output"))

    def _pump_stderr(self) -> None:
        """Forward the child's stderr, and always keep a tail of it.

        Gating a server relays its logs, because an operator debugging their own MCP server needs
        them. *Discovering* one does not: `neti init` launches a dozen servers in a row purely to
        ask `tools/list`, and their startup banners interleaved with its progress output made a
        working scan look like it was malfunctioning. So the echo is optional and the tail is not.
        """
        stderr = self._proc.stderr
        assert stderr is not None
        for line in stderr:
            self.stderr_tail = [*self.stderr_tail[-9:], line.rstrip()]
            if self._echo_stderr:
                sys.stderr.write(line)
                sys.stderr.flush()

    def _fail_pending(self, exc: BaseException) -> None:
        with self._pending_lock:
            waiting = list(self._pending.values())
            self._pending.clear()
        for future in waiting:
            if not future.done():
                future.set_exception(exc)


def serve_stdio(
    gateway: McpGateway,
    *,
    upstream: StdioUpstream | None = None,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
) -> None:
    """Run the gate as an MCP server on this process's stdin/stdout.

    Pass `upstream` so the child's own server→client traffic goes out through the same locked
    writer as our responses. Two writers to one stdout is a torn frame waiting to happen.

    Returns when the client closes stdin, which is how every MCP client says it is done.
    """
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    out_lock = threading.Lock()

    def emit(message: dict[str, Any]) -> None:
        _write(sink, out_lock, message)

    if upstream is not None:
        upstream.on_unsolicited = emit

    def handle(message: dict[str, Any]) -> None:
        try:
            response = gateway.handle(message)
        except Exception as exc:
            print(f"neti: gate error: {exc}", file=sys.stderr)
            if "id" in message:
                emit(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32603, "message": f"neti gate error: {exc}"},
                    }
                )
            return
        if response is not None:
            emit(response)

    with ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="neti-gate") as pool:
        for line in source:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                # A parse error genuinely is a protocol error, unlike a gate denial.
                emit(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"parse error: {exc}"},
                    }
                )
                continue
            if isinstance(message, dict):
                pool.submit(handle, message)
