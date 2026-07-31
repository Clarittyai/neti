"""The control plane's state. SQLite, because a POC that needs Postgres to start is not a POC.

This is the paid artifact (BUSL-1.1, see LICENSING.md) and it exists for one reason: a second
person approving a call needs somewhere the request can go and somewhere the answer can come back.
That is shared state, and shared state is what a single machine does not have.

The semantics have to match `test_approvals.py::FakeApprover` exactly, because that fake is what
the gate was written against. Where the two differ, this one is wrong.

Two of them are worth defending in SQL rather than in prose:

**Redemption is atomic.** `UPDATE ... WHERE redeemed = 0` and check the row count. Doing it as a
read then a write leaves a window where two agents redeem one grant, which is precisely the
"approve once, run twice" hole single-use exists to close. SQLite gives us the atomicity for free;
throwing it away by reading first would be a choice.

**Growth is refused, not clamped.** If the target now resolves to more than the human approved, the
grant does not partially apply — it is over. An approval is a statement about a specific number a
person actually looked at.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

__all__ = ["ApprovalRow", "Store"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id                 TEXT PRIMARY KEY,
    digest             TEXT NOT NULL,
    state              TEXT NOT NULL,
    approved_magnitude INTEGER,
    unit               TEXT,
    evidence           TEXT NOT NULL,
    requested_at       TEXT NOT NULL,
    expires_at         TEXT NOT NULL,
    decided_by         TEXT,
    decided_at         TEXT,
    reason             TEXT,
    redeemed           INTEGER NOT NULL DEFAULT 0,
    redeemed_at        TEXT
);
-- One live request per call. A retry must find the existing grant rather than summon the reviewer
-- again, and the index is what makes that lookup both correct and cheap.
CREATE INDEX IF NOT EXISTS approvals_digest ON approvals (digest);
CREATE INDEX IF NOT EXISTS approvals_state ON approvals (state);
"""


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ApprovalRow:
    id: str
    digest: str
    state: str
    approved_magnitude: int | None
    unit: str | None
    evidence: dict[str, Any]
    requested_at: str
    expires_at: str
    decided_by: str | None = None
    decided_at: str | None = None
    reason: str | None = None
    redeemed: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "digest": self.digest,
            "state": self.state,
            "approved_magnitude": self.approved_magnitude,
            "unit": self.unit,
            "evidence": self.evidence,
            "requested_at": self.requested_at,
            "expires_at": self.expires_at,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "reason": self.reason,
            "redeemed": self.redeemed,
        }


class Store:
    def __init__(self, path: str | Path = "neti-cloud.db", *, ttl_s: int = 900) -> None:
        self.ttl_s = ttl_s
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------------ approvals

    LIVE = ("pending", "granted", "denied")
    """The states a retry has to be shown.

    `denied` is in this list and it is the one that matters. Leave it out and a refused call finds
    nothing on its next attempt, raises a *fresh* request, and shows the agent `pending` again — so
    a reviewer's "no" is erased by a retry and the same person is asked the same question forever.
    A denial has to stand until it expires, because that is what saying no means.

    `redeemed` and `expired` are excluded for the opposite reason: a spent grant must not make a
    retry look approved, and a request nobody answered before it lapsed should be raised again.
    """

    def open_for(self, digest: str) -> ApprovalRow | None:
        """The standing answer for this exact call, if there is one."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM approvals WHERE digest = ? ORDER BY requested_at DESC", (digest,)
            ).fetchall()
        for row in rows:
            found = self._expire_if_due(_row(row))
            if not found.redeemed and found.state in self.LIVE:
                return found
        return None

    def request(
        self, digest: str, evidence: dict[str, Any], magnitude: int | None, unit: str | None
    ) -> ApprovalRow:
        existing = self.open_for(digest)
        if existing is not None:
            return existing

        now = _now()
        row = ApprovalRow(
            id=f"a_{uuid.uuid4().hex[:12]}",
            digest=digest,
            state="pending",
            approved_magnitude=magnitude,
            unit=unit,
            evidence=evidence,
            requested_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=self.ttl_s)).isoformat(),
        )
        with self._lock:
            self._db.execute(
                "INSERT INTO approvals (id, digest, state, approved_magnitude, unit, evidence,"
                " requested_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    row.id,
                    row.digest,
                    row.state,
                    row.approved_magnitude,
                    row.unit,
                    json.dumps(row.evidence),
                    row.requested_at,
                    row.expires_at,
                ),
            )
            self._db.commit()
        return row

    def get(self, approval_id: str) -> ApprovalRow | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        return None if row is None else self._expire_if_due(_row(row))

    def decide(
        self, approval_id: str, *, granted: bool, decided_by: str, reason: str | None = None
    ) -> ApprovalRow | None:
        """A human answers. Only a pending request can be answered.

        The `state = 'pending'` guard stops a second reviewer overturning the first, and stops a
        decision landing on a grant that has already been spent.
        """
        state = "granted" if granted else "denied"
        with self._lock:
            cursor = self._db.execute(
                "UPDATE approvals SET state = ?, decided_by = ?, decided_at = ?, reason = ?"
                " WHERE id = ? AND state = 'pending'",
                (state, decided_by, _now().isoformat(), reason, approval_id),
            )
            self._db.commit()
            changed = cursor.rowcount
        return self.get(approval_id) if changed else None

    def redeem(self, approval_id: str, magnitude: int | None) -> ApprovalRow:
        """Spend a grant on one execution.

        Returns the approval in its resulting state — the caller reads it rather than catching an
        exception, because "this grant no longer applies" is an ordinary answer and not a fault.
        """
        found = self.get(approval_id)
        if found is None:
            raise KeyError(approval_id)
        if found.state != "granted":
            return found
        if found.redeemed:
            return _refused(found, "already redeemed")

        # An approval is a statement about a number a person actually looked at. If the target has
        # grown since, that statement no longer covers this call — refuse rather than clamp.
        if found.approved_magnitude is not None and (
            magnitude is None or magnitude > found.approved_magnitude
        ):
            return _refused(found, "target grew past the approved magnitude")

        with self._lock:
            cursor = self._db.execute(
                "UPDATE approvals SET redeemed = 1, redeemed_at = ? WHERE id = ? AND redeemed = 0",
                (_now().isoformat(), approval_id),
            )
            self._db.commit()
            won = cursor.rowcount
        # Lost the race: another agent spent this grant between the read above and here. The UPDATE
        # is the arbiter, not the read.
        return found if won else _refused(found, "already redeemed")

    def list(self, state: str | None = None, limit: int = 100) -> list[ApprovalRow]:
        sql = "SELECT * FROM approvals"
        params: tuple[Any, ...] = ()
        if state:
            sql += " WHERE state = ?"
            params = (state,)
        sql += " ORDER BY requested_at DESC LIMIT ?"
        with self._lock:
            rows = self._db.execute(sql, (*params, limit)).fetchall()
        return [self._expire_if_due(_row(r)) for r in rows]

    # ------------------------------------------------------------------ internals

    def _expire_if_due(self, row: ApprovalRow) -> ApprovalRow:
        """Expiry is evaluated on read rather than swept on a timer.

        A grant nobody has looked at since it lapsed is not a problem, and a background sweeper is a
        second source of truth about time. Reading is the only moment expiry can matter.
        """
        if row.state != "pending" or _now() < datetime.fromisoformat(row.expires_at):
            return row
        with self._lock:
            self._db.execute(
                "UPDATE approvals SET state = 'expired' WHERE id = ? AND state = 'pending'",
                (row.id,),
            )
            self._db.commit()
        return ApprovalRow(**{**row.__dict__, "state": "expired", "reason": "expired unanswered"})


def _row(row: sqlite3.Row) -> ApprovalRow:
    return ApprovalRow(
        id=row["id"],
        digest=row["digest"],
        state=row["state"],
        approved_magnitude=row["approved_magnitude"],
        unit=row["unit"],
        evidence=json.loads(row["evidence"]),
        requested_at=row["requested_at"],
        expires_at=row["expires_at"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        reason=row["reason"],
        redeemed=bool(row["redeemed"]),
    )


def _refused(row: ApprovalRow, reason: str) -> ApprovalRow:
    """A grant that no longer applies. Reported as `expired` rather than `denied`, because nobody
    declined this call — the grant simply stopped covering it."""
    return ApprovalRow(**{**row.__dict__, "state": "expired", "reason": reason})
