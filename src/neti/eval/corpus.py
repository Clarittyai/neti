"""Turning a real agent session into traffic that can be re-run somewhere else.

The demo needs a week of agent behaviour and an evaluator has none, so something has to supply it.
The choice of *what* is the whole credibility question, and there are only three honest answers:

1. a real session, captured and replayed — real behaviour, real distribution
2. authored calls against real targets — real data, invented behaviour
3. a live agent — real everything, unrepeatable

This module is (1). The capture costs nothing to produce, because **the hook already records every
call**: run `neti gate`/`neti hook` in observe mode for an afternoon and the decision log *is* the
session. This reads that log back out.

**What is kept, and what is thrown away.** A corpus keeps the tool, the gated parameter and the
target *relative to the repository root* — enough to re-run the call somewhere else. It discards
absolute paths, home directories, hostnames and machine names, because a corpus is a thing people
share and a path like `/Users/someone/clients/acme/...` is not something to hand over casually.

**Replay re-resolves; it does not re-derive.** `insight/replay.py` re-runs `decide` over the
*stored* resolutions to check whether the decision procedure still reaches the same verdict. This
re-runs the *calls* against new files, producing new magnitudes. Two different questions and the
words are kept apart deliberately: **re-derive** (verify) versus **re-run** (demo).

A path in the corpus that does not exist in the target repository resolves UNRESOLVED and is
counted. That is honest — it demonstrates the declared `on_unresolved` doing its job — and a corpus
that mostly misses is a corpus the demo should say is a poor fit rather than quietly average away.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from neti.core.record import DecisionRecord

__all__ = ["Call", "Corpus", "capture", "load_corpus", "rebase", "write_corpus"]


@dataclass(frozen=True)
class Call:
    """One tool call, stripped to what can be re-run elsewhere."""

    tool: str
    pointer: str
    """The JSON pointer of the gated parameter, e.g. `/pattern`."""

    target: str
    """Relative to the repository root, with `/` separators regardless of who captured it."""

    unit: str = ""
    """What the original resolution counted, kept for reporting rather than for replay."""

    def args(self, root: Path) -> dict[str, Any]:
        """The call's arguments, rebased onto `root`."""
        key = self.pointer.lstrip("/")
        return {key: str(rebase(self.target, root))}


@dataclass(frozen=True)
class Corpus:
    calls: tuple[Call, ...]
    source: str = ""
    """Where it came from, in a form safe to publish: "a Claude Code session, 3 hours" — never a
    path, a hostname or a repository name."""

    def __len__(self) -> int:
        return len(self.calls)

    @property
    def tools(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for call in self.calls:
            counts[call.tool] = counts.get(call.tool, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def rebase(target: str, root: Path) -> Path:
    """A corpus target, resolved against the repository being demoed on."""
    return root / PurePosixPath(target)


def _relative(target: str, root: Path) -> str | None:
    """`target` relative to `root`, or `None` when it is outside it.

    Outside means: a path in somebody's home directory, a system file, a sibling checkout. Those
    are exactly what a corpus must not carry, so they are dropped rather than sanitised — a
    half-scrubbed path is worse than a missing one because it looks safe.
    """
    try:
        resolved = Path(target).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return PurePosixPath(relative).as_posix() or "."


def capture(records: Iterable[DecisionRecord], root: Path, *, source: str = "") -> Corpus:
    """Read a decision log back out as re-runnable traffic.

    Only calls whose target sits inside `root` survive, which is both the privacy rule and the
    usefulness one: a path outside the repository cannot be rebased onto a different repository.
    """
    calls: list[Call] = []
    for record in records:
        for cause in record.causes:
            target = cause.get("target")
            if not isinstance(target, str) or not target:
                continue
            relative = _relative(target, root)
            if relative is None:
                continue
            calls.append(
                Call(
                    tool=record.tool,
                    pointer=str(cause["pointer"]),
                    target=relative,
                    unit=str(cause.get("unit") or ""),
                )
            )
    return Corpus(calls=tuple(calls), source=source)


def write_corpus(corpus: Corpus, path: Path) -> None:
    """One JSON object per line, so a human can read it before trusting it.

    A corpus is a thing somebody is asked to run against their own repository. It should be
    reviewable in a terminal without a parser, which rules out anything more compact.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"source": corpus.source, "calls": len(corpus.calls)}, sort_keys=True)]
    lines += [
        json.dumps(
            {"tool": c.tool, "pointer": c.pointer, "target": c.target, "unit": c.unit},
            sort_keys=True,
        )
        for c in corpus.calls
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_corpus(path: Path) -> Corpus:
    rows: Iterator[dict[str, Any]] = (
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    header = next(rows, {})
    if "calls" not in header:  # headerless file — treat every line as a call
        rows = (
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        header = {}
    return Corpus(
        calls=tuple(
            Call(
                tool=str(r["tool"]),
                pointer=str(r["pointer"]),
                target=str(r["target"]),
                unit=str(r.get("unit") or ""),
            )
            for r in rows
        ),
        source=str(header.get("source") or ""),
    )
