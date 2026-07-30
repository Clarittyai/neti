"""NDJSON record sink and reader.

Append-only newline-delimited JSON, not a database. A decision record is written once and never
updated, the whole file is a hash chain, and the analysis surface is `neti report` — none of which
wants a schema migration. Postgres is a later problem and would not change any measurement.

Writes are buffered on a queue and flushed by a background thread so a disk stall cannot appear as
gate latency. Losing the tail of the queue on a hard kill is acceptable for an observe-mode POC and
is called out in `close()`; an enforce deployment that treats records as compliance evidence needs
fsync-per-record, which is a deliberate future trade rather than something to leave implicit.
"""

from __future__ import annotations

import json
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

    def __init__(self, path: str | Path, *, batch: int = 64) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue[Any] = queue.Queue()
        self._batch = batch
        self._closed = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="neti-jsonl-sink")
        self._thread.start()

    def write(self, record: DecisionRecord) -> None:
        if self._closed:
            raise RuntimeError("sink is closed")
        self._queue.put(record)

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
