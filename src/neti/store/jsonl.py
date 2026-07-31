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

import fcntl
import json
import os
import queue
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from neti.core.record import DecisionRecord

__all__ = ["JsonlSink", "chain_head", "read_records"]

_SENTINEL = object()


class JsonlSink:
    """Append decision records to a file, off the hot path."""

    def __init__(self, path: str | Path, *, batch: int = 64, buffered: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
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

        `flock` rather than a thread lock, because the writers are separate *processes* — one per
        tool call, when the gate is a `PreToolUse` hook. A `threading.Lock` would have looked
        entirely correct and prevented nothing.
        """
        with self.path.open("a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.seek(0)
                head: str | None = None
                for line in fh:
                    if line.strip():
                        head = json.loads(line)["record_digest"]
                sealed = record.sealed(head)
                fh.seek(0, os.SEEK_END)
                fh.write(json.dumps(sealed.model_dump(mode="json", by_alias=True)) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
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
    try:
        last = None
        for record in read_records(path):
            last = record
        return None if last is None else last.record_digest
    except FileNotFoundError:
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
