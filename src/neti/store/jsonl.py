"""NDJSON record sink and reader.

Append-only newline-delimited JSON, not a database. A decision record is written once and never
updated, the whole file is a hash chain, and the analysis surface is `neti report` — none of which
wants a schema migration. Postgres is a later problem and would not change any measurement.

**The sink owns the chaining, and only it can.** A record's `prev_digest` has to be the digest of
whatever is actually last in the file *at the moment it is appended* — not at the moment the engine
was constructed. Those are different instants, and the gap between them is where
this broke: Claude Code runs tool calls in parallel, each hook invocation is its own process, two of
them read the same head, and both appended from it. One `prev_digest` claimed by two records, and
`neti verify` correctly called the result a broken chain. It was found by pointing the hook at a
real agent; no single-writer test can produce it.

So `write` takes an exclusive lock on the file, re-reads the head *under* that lock, re-seals the
record against it, and appends. Sealing is cheap — one blake2b over data already in hand — and the
lock is held across a few milliseconds of I/O, never across a resolver call.

That costs the old behaviour, which buffered on a queue and flushed from a background thread so a
disk stall could not appear as gate latency. Correctness wins: an audit chain that forks under
concurrency is not an audit chain. `buffered=True` restores the old path for a caller that is
genuinely the only writer and would rather have the latency.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

from neti.core.record import DecisionRecord

# A writer holds the lock for microseconds, so a reader that collides with one only has to wait
# out a moment. Bounded rather than indefinite: a gate that hangs is a gate that gets removed.
_HEAD_READ_ATTEMPTS = 5
_HEAD_READ_BACKOFF_S = 0.02

__all__ = ["JsonlSink", "chain_head", "read_records"]

_SENTINEL = object()


@contextmanager
def _exclusive(fh: IO[str]) -> Iterator[None]:
    """Lock a file across processes, on whichever platform this is.

    `fcntl` is Unix-only and `msvcrt` is Windows-only, so importing either at module scope makes the
    package unimportable on the other. Worth spelling out because it nearly shipped: the lock went
    in on macOS, and a top-level `import fcntl` would have made `import neti` raise on every Windows
    machine — while `neti init` carries a Windows branch for finding Claude Desktop's config, so we
    plainly expect to run there.

    Branching on `sys.platform` rather than catching `ImportError`, because that is the form the
    type checker understands: it evaluates only the branch for the platform it is checking, which is
    how the Windows call gets checked at all instead of being hidden inside an except clause.

    **A failure to lock is not a failure to record.** If the filesystem refuses — some network
    mounts do — the append still happens. It is simply no longer serialised, which is the pre-lock
    behaviour and strictly better than dropping the decision on the floor.
    """
    if sys.platform == "win32":
        import msvcrt

        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        except OSError:
            yield
            return
        try:
            yield
        finally:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            yield
            return
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _own_only(path: Path) -> None:
    """Create the record file if needed and keep it readable only by its owner.

    It defaulted to whatever the umask allowed, which on most machines is 0644 — world-readable.
    That file holds every tool call every agent on the box has made, including the arguments, and
    on a shared or multi-tenant host that is an audit log anyone can read.

    Best-effort on purpose. A filesystem that cannot express the mode — a Windows share, a mounted
    volume — must not stop the gate from recording, because refusing to write is refusing to gate.
    The permission is a defence, not the defence: `core/redact.py` is what keeps credentials out of
    the contents in the first place.
    """
    try:
        if not path.exists():
            path.touch(mode=0o600)
        else:
            path.chmod(0o600)
    except OSError:
        pass


class JsonlSink:
    """Append decision records to a file, off the hot path."""

    def __init__(self, path: str | Path, *, batch: int = 64, buffered: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _own_only(self.path)
        self._buffered = buffered
        self._closed = False
        self._queue: queue.Queue[Any] = queue.Queue()
        self._batch = batch
        self._thread: threading.Thread | None = None
        if buffered:
            self._thread = threading.Thread(target=self._run, daemon=True, name="neti-jsonl-sink")
            self._thread.start()

    def write(self, record: DecisionRecord) -> DecisionRecord:
        """Append, and return the record as it was actually written.

        The returned record is the one on disk: re-sealed against the true head, so its
        `record_digest` is the one `neti verify` will recompute. A caller that reports a digest to
        anyone must report this one and not the record it passed in.
        """
        if self._closed:
            raise RuntimeError("sink is closed")
        if self._buffered:
            self._queue.put(record)
            return record
        return self._sealed_append(record)

    def _sealed_append(self, record: DecisionRecord) -> DecisionRecord:
        """Read the head and append, atomically with respect to every other writer.

        A *file* lock rather than a thread lock, because the writers are separate **processes** —
        one per tool call, when the gate is a `PreToolUse` hook. A `threading.Lock` would have
        looked entirely correct and prevented nothing.

        The head comes from the sidecar when it is current, and from a full walk when it is not.
        Both happen under the lock, so a writer never seals against a head another writer has
        already moved.
        """
        with self.path.open("a+", encoding="utf-8") as fh, _exclusive(fh):
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            cached = _cached_head(self.path, size)
            head: str | None
            if isinstance(cached, _Miss):
                fh.seek(0)
                head = None
                for line in fh:
                    if line.strip():
                        head = json.loads(line)["record_digest"]
            else:
                head = cached
            sealed = record.sealed(head)
            fh.seek(0, os.SEEK_END)
            fh.write(json.dumps(sealed.model_dump(mode="json", by_alias=True)) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            _write_head(self.path, fh.tell(), sealed.record_digest)
        return sealed

    def _run(self) -> None:
        pending: list[str] = []
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                self._flush(pending)
                return
            pending.append(json.dumps(item.model_dump(mode="json", by_alias=True)))
            if len(pending) >= self._batch or self._queue.empty():
                self._flush(pending)
                pending = []

    def _flush(self, pending: list[str]) -> None:
        if not pending:
            return
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(pending) + "\n")

    def close(self, timeout: float = 5.0) -> None:
        """Drain and stop.

        Anything still queued after `timeout` is lost — see the module docstring.
        """
        if self._closed:
            return
        self._closed = True
        if self._thread is not None:
            self._queue.put(_SENTINEL)
            self._thread.join(timeout)

    def __enter__(self) -> JsonlSink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class _Miss:
    """Distinct from `None`, which is a legitimate head: the empty chain."""


_MISS = _Miss()


def _head_path(path: Path) -> Path:
    return path.with_name(path.name + ".head")


def _cached_head(path: Path, size: int) -> str | _Miss | None:
    """The last digest, if the sidecar still describes this exact file.

    Keyed on the records file's byte length. Anything that appended, truncated or rewrote the file
    without going through the sink changes the length, the key stops matching, and every reader
    falls back to the full walk — so the cache can be stale, deleted or garbage without ever
    producing a wrong answer. It is an optimisation that fails to *slow*, never to wrong.
    """
    try:
        cached = json.loads(_head_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _MISS
    if not isinstance(cached, dict) or cached.get("bytes") != size:
        return _MISS
    digest = cached.get("digest")
    return digest if isinstance(digest, str) or digest is None else _MISS


def _write_head(path: Path, size: int, digest: str | None) -> None:
    """Record the new head. Best effort: losing it costs a walk, never a correct answer."""
    with contextlib.suppress(OSError):
        _head_path(path).write_text(json.dumps({"bytes": size, "digest": digest}), encoding="utf-8")


def chain_head(path: str | Path) -> str | None:
    """Digest of the last record in an existing file, or `None` if there is no file yet.

    A process that appends to an existing record file has to continue that file's chain. Without
    this, every restart writes a record whose `prev_digest` is `None` in the middle of the chain,
    and `verify_chain` correctly reports a break — a break caused by a restart rather than by
    tampering, which is the worst possible false alarm an audit surface can raise.

    Reads the whole file. That is fine at POC volumes and honest about what it costs; a deployment
    with millions of records wants the head cached alongside, not a tail-seek that has to cope with
    partial final lines.
    """
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError:
        return None

    cached = _cached_head(target, size)
    if not isinstance(cached, _Miss):
        return cached

    # Retried, because on Windows this raises where on Unix it would simply read.
    #
    # `msvcrt.locking` is a *mandatory* lock: while a writer holds it, another process cannot open
    # the file for reading at all and gets `PermissionError`. `fcntl.flock` is advisory and readers
    # sail past it, which is why this was invisible until the first Windows CI run. Claude Code
    # issues tool calls in parallel and every hook is its own process, so two agents starting at
    # once is the ordinary case there, not a corner: one of them died with a traceback before the
    # gate had decided anything.
    #
    # Giving up after the retries is safe, and that is worth being explicit about because returning
    # `None` looks like it should fork the chain. It cannot: this value only seeds the *first*
    # append, and `JsonlSink.append` re-reads the head inside its own exclusive lock before sealing.
    # The authority is there, not here.
    for attempt in range(_HEAD_READ_ATTEMPTS):
        try:
            last = None
            for record in read_records(path):
                last = record
            return None if last is None else last.record_digest
        except FileNotFoundError:
            return None
        except PermissionError:
            if attempt == _HEAD_READ_ATTEMPTS - 1:
                return None
            time.sleep(_HEAD_READ_BACKOFF_S * (attempt + 1))
    return None


def read_records(path: str | Path) -> Iterator[DecisionRecord]:
    """Stream records back.

    A malformed line raises rather than being skipped: this file is a hash chain, and silently
    stepping over a line would make `neti verify` report a break it cannot explain.
    """
    with Path(path).open(encoding="utf-8") as fh:
        for number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                yield DecisionRecord.model_validate(json.loads(line))
            except Exception as exc:
                raise ValueError(f"{path}:{number}: unreadable decision record: {exc}") from exc
