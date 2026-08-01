"""How many rows would this statement destroy.

`SCOPE.md` NC-10 said row-count gating was not covered, on two grounds that are both correct:
`EXPLAIN` estimates are biased *low* — the dangerous direction — and `ON DELETE CASCADE` fan-out is
invisible to a query plan. This resolver does not contradict either. It sidesteps the first and
absorbs the second into the direction, and NC-10 is narrowed to say exactly what is still open.

**No EXPLAIN.** The count comes from `SELECT count(*) FROM <table> WHERE <predicate>` — the same
thing a careful operator types before a `DELETE`. That is a real scan against real data rather than
a planner's guess, so the low bias NC-10 objects to never enters the picture. It costs what the scan
costs, which is the honest price of knowing.

**Always a LOWER_BOUND, never EXACT.** The count is exact for the rows the predicate names, and
cascades add rows it cannot see. So the truth is always at least the measured value, which is what
`LOWER_BOUND` means and why it is sound here rather than a hedge: sound to block on, never sound to
allow on. A statement that would take 40,000 rows directly plus an unknown cascade cannot pass a
10,000-row ceiling, and a statement measuring 3 rows does not get to claim it is small.

**It refuses far more than it accepts, on purpose.** NC-10's deeper objection is that a gate reading
a *string* makes a weaker claim than one reading a value, and it stands. So the recogniser handles
two shapes — `DELETE FROM t [WHERE p]` and `UPDATE t SET ... [WHERE p]` — and anything else, or
anything ambiguous, resolves UNRESOLVED and routes through the declared `on_unresolved`. Multiple
statements, comments, `DELETE ... USING`, `UPDATE ... FROM` and joins are all rejected outright. A
mis-parse would produce a count of the *wrong* predicate and a confident wrong verdict, which is
worse than no answer at all; the only defence is to decline everything not certainly understood.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Protocol

from neti.core.types import Resolution
from neti.core.units import Direction, Unit
from neti.resolvers.base import ResolveContext

__all__ = [
    "CountRunner",
    "DbapiCountRunner",
    "EnvCountRunner",
    "RowsResolver",
    "Statement",
    "parse_statement",
]


@dataclass(frozen=True)
class Statement:
    """A statement we are certain we understood."""

    verb: str
    table: str
    predicate: str | None
    """`None` means no WHERE clause — the whole table."""


class CountRunner(Protocol):
    """The database seam, so this is testable against sqlite without a server."""

    def count(self, table: str, predicate: str | None) -> int: ...


# A table name: bare, schema-qualified, or quoted either way. Anything with a space, a paren or a
# comma in it is not a name we will accept.
_NAME = r'(?:[A-Za-z_][A-Za-z0-9_$]*|"[^"]+"|`[^`]+`|\[[^\]]+\])'
_TABLE = rf"{_NAME}(?:\.{_NAME})*"

_DELETE = re.compile(rf"^\s*DELETE\s+FROM\s+({_TABLE})\s*(.*)$", re.IGNORECASE | re.DOTALL)
_UPDATE = re.compile(rf"^\s*UPDATE\s+({_TABLE})\s+SET\s+(.*)$", re.IGNORECASE | re.DOTALL)

_WHERE_PREFIX = re.compile(r"WHERE(?![A-Za-z0-9_$])", re.IGNORECASE)
"""The word WHERE and not merely something starting with those five letters. Without the boundary,
`DELETE FROM t WHEREVER = 1` parses as the predicate `VER = 1` and counts a real, wrong number."""

_REJECTED = re.compile(r"\b(USING|JOIN|RETURNING)\b", re.IGNORECASE)
"""Constructs that change which rows a statement touches in ways this recogniser does not model.
`DELETE ... USING` and `UPDATE ... FROM` are multi-table; a count of the named table alone would be
wrong, and wrong in the permissive direction."""


def _strip_trailing_semicolon(sql: str) -> str | None:
    """One statement, or nothing. Returns `None` if there is more than one."""
    body, _, tail, unterminated = _scan(sql)
    if tail or unterminated:
        return None
    return body.rstrip().rstrip(";")


def _scan(sql: str) -> tuple[str, int, str, bool]:
    """Walk the statement respecting quotes.

    Returns (first statement, WHERE offset, remainder, ended inside a quote).

    Written by hand rather than with a regex because both jobs it does are jobs a regex gets wrong
    in the same way: `UPDATE t SET note = 'delete where you like'` contains both a `;`-free clause
    and the word WHERE inside a string literal. A scanner that does not track quote state finds
    that WHERE, counts the wrong predicate, and reports a confident wrong number.
    """
    where_at = -1
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"`":
            quote = ch
            i += 1
            while i < n:
                if sql[i] == quote:
                    # Doubled quote is an escaped quote, not the end of the literal.
                    if i + 1 < n and sql[i + 1] == quote:
                        i += 2
                        continue
                    break
                i += 1
            # Step past the closing quote. Without this the closing quote is read as *opening* a
            # new literal, which silently swaps which halves of the statement are considered quoted
            # and hides every real WHERE after the first string.
            i += 1
            if i > n:
                # Ran off the end inside a literal. The statement is malformed, and a malformed
                # statement must not be parsed optimistically — the predicate we would extract is
                # not the one the database would see.
                return sql, where_at, "", True
            continue
        if ch == ";":
            rest = sql[i + 1 :].strip()
            return sql[:i], where_at, rest, False
        if where_at < 0 and (ch in "wW") and sql[i : i + 5].upper() == "WHERE":
            before_ok = i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_")
            after = sql[i + 5 : i + 6]
            if before_ok and (after == "" or not (after.isalnum() or after == "_")):
                where_at = i
        i += 1
    return sql, where_at, "", False


def parse_statement(sql: str) -> Statement | None:
    """A `Statement` when we are certain, `None` in every other case.

    `None` is not a failure mode to be minimised — it is most of the intended behaviour. See the
    module docstring: the cost of a wrong parse is a confident wrong verdict.
    """
    if not sql or not sql.strip():
        return None
    # Comments can hide a second statement or a predicate from the scanner, and no legitimate
    # tool-call argument needs them.
    if "--" in sql or "/*" in sql:
        return None

    single = _strip_trailing_semicolon(sql)
    if single is None:
        return None

    match = _DELETE.match(single)
    if match:
        table, rest = match.group(1), match.group(2)
        if _REJECTED.search(rest) or (rest.strip() and not _WHERE_PREFIX.match(rest.lstrip())):
            return None
        return Statement("DELETE", table, _predicate(rest))

    match = _UPDATE.match(single)
    if match:
        table, rest = match.group(1), match.group(2)
        if _REJECTED.search(rest) or re.search(r"\bFROM\b", rest, re.IGNORECASE):
            return None
        _, where_at, _, _ = _scan(rest)
        if where_at < 0:
            return Statement("UPDATE", table, None)
        return Statement("UPDATE", table, rest[where_at + 5 :].strip() or None)

    return None


def _predicate(rest: str) -> str | None:
    stripped = rest.strip()
    if not stripped:
        return None
    return stripped[5:].strip() or None  # drop the leading WHERE


@dataclass
class DbapiCountRunner:
    """Counts against any PEP 249 connection — sqlite3, psycopg, mysqlclient.

    **The predicate is interpolated, not bound**, because it is a predicate and not a value; there
    is no parameter to bind. That is only safe because of what the recogniser already refused: a
    statement containing `;` or a comment marker never reaches here, so the predicate cannot
    terminate the `SELECT` and start something else. Both halves of that are tested.

    Point it at a read-only connection or a replica anyway. Belt and braces is the correct posture
    for the one component in this product that composes SQL.
    """

    connection: object

    def count(self, table: str, predicate: str | None) -> int:
        sql = f"select count(*) from {table}"
        if predicate:
            sql += f" where {predicate}"
        cursor = self.connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute(sql)
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row is None:
            raise RuntimeError(f"count returned no row for {table}")
        return int(row[0])


@dataclass
class EnvCountRunner:
    """A `CountRunner` that connects from `NETI_DATABASE_URL` on first use.

    Lazy so that registering `db.rows` costs nothing and never fails at import. A policy that binds
    it without the variable set gets an UNRESOLVED on that one gate, carrying a reason that says
    what to export — rather than an exception at construction that takes down the resolvers a
    coding agent needs and that have no credentials at all.

    **Point it at a read-only user.** This composes SQL from an agent's own statement, and while the
    recogniser refuses anything that could escape the `SELECT`, a read-only grant is the check that
    does not depend on the recogniser being right.
    """

    _connection: object | None = None

    def count(self, table: str, predicate: str | None) -> int:
        if self._connection is None:
            self._connection = _connect_from_env()
        return DbapiCountRunner(self._connection).count(table, predicate)


def _connect_from_env() -> object:
    import os

    dsn = os.environ.get("NETI_DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "db.rows needs NETI_DATABASE_URL (sqlite:///path or postgresql://…); "
            "point it at a read-only user"
        )
    if dsn.startswith("sqlite://"):
        import sqlite3

        return sqlite3.connect(dsn.removeprefix("sqlite://").lstrip("/") or ":memory:")
    if dsn.startswith(("postgres://", "postgresql://")):
        try:
            import psycopg  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "db.rows needs psycopg for postgres; install `neti[database]`"
            ) from exc
        return psycopg.connect(dsn)
    raise RuntimeError(f"unsupported NETI_DATABASE_URL scheme: {dsn.split('://', 1)[0]!r}")


@dataclass
class RowsResolver:
    """Counts the rows a `DELETE` or `UPDATE` would touch, by counting them.

    `reachable_hint` is what `neti inventory` reports: the total rows this credential could reach.
    Left `None` unless declared, because computing it means counting every table.
    """

    runner: CountRunner
    reachable_hint: int | None = None

    unit: ClassVar[Unit] = Unit.ROWS
    breakdown_keys: ClassVar[frozenset[str]] = frozenset()

    def resolve(self, target: str, ctx: ResolveContext) -> Resolution:
        del ctx
        statement = parse_statement(target)
        if statement is None:
            return Resolution.unresolved(
                self.unit,
                reason="statement_not_recognised",
                evidence={
                    "sql": target[:200],
                    "recognised": "DELETE FROM t [WHERE p] | UPDATE t SET ... [WHERE p]",
                    "why": (
                        "a mis-parsed statement produces a count of the wrong predicate and a "
                        "confident wrong verdict, so anything not certainly understood is declined"
                    ),
                },
            )

        try:
            rows = self.runner.count(statement.table, statement.predicate)
        except Exception as exc:  # a database we cannot query is not a table with no rows in it
            return Resolution.unresolved(
                self.unit,
                reason="count_failed",
                evidence={"error": str(exc)[:200], "table": statement.table},
            )

        return Resolution.resolved(
            self.unit,
            rows,
            # Never EXACT. ON DELETE CASCADE fan-out is invisible to this count and can only add
            # rows, so the truth is at or above it — which is exactly what LOWER_BOUND asserts.
            direction=Direction.LOWER_BOUND,
            resolved_at=datetime.now(UTC),
            consistency="eventual",
            evidence={
                "verb": statement.verb,
                "table": statement.table,
                "predicate": statement.predicate,
                "counted_by": "select count(*)",
                "excludes": "ON DELETE CASCADE fan-out (SCOPE.md NC-10)",
            },
        )

    def reachable_max(self, ctx: ResolveContext) -> Resolution:
        del ctx
        if self.reachable_hint is None:
            return Resolution.unresolved(
                self.unit,
                reason="no_reachable_hint_declared",
                evidence={
                    "hint": (
                        "the rows this credential could reach means counting every table it can "
                        "read; declare the figure if you want it on the inventory"
                    )
                },
            )
        return Resolution.resolved(
            self.unit,
            self.reachable_hint,
            direction=Direction.UPPER_BOUND,
            resolved_at=datetime.now(UTC),
            consistency="eventual",
            evidence={"basis": "operator-declared"},
        )
