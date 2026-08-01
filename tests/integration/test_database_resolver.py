"""Row counting, and the much larger set of statements it refuses to count.

`SCOPE.md` NC-10 is the reason this file is shaped the way it is. Its objection to row-count gating
is that a gate reading a *string* makes a weaker claim than one reading a value — a mis-parse
produces a count of the wrong predicate and a confident wrong verdict, which is strictly worse than
declining. So the refusals are the load-bearing tests, and the one that matters most is
`test_a_where_inside_a_string_literal_is_not_a_predicate`: it is the case a regex gets wrong while
looking completely correct.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from neti.core.units import Direction, Unit, may_allow, may_block
from neti.core.verdict import ResolutionState
from neti.resolvers.base import ResolveContext
from neti.resolvers.database import DbapiCountRunner, RowsResolver, parse_statement

CTX = ResolveContext()


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.execute("create table users (id integer primary key, org text, note text)")
    conn.executemany(
        "insert into users (org, note) values (?, ?)",
        [("acme", "x") for _ in range(1200)] + [("other", "y") for _ in range(3)],
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def resolver(db: sqlite3.Connection) -> RowsResolver:
    return RowsResolver(DbapiCountRunner(db))


# ---------------------------------------------------------------------------- counting


def test_it_counts_the_rows_a_delete_would_take(resolver: RowsResolver) -> None:
    out = resolver.resolve("DELETE FROM users WHERE org = 'acme'", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == 1_200
    assert out.unit is Unit.ROWS


def test_a_delete_with_no_where_clause_is_the_whole_table(resolver: RowsResolver) -> None:
    """The single most dangerous statement an agent can emit, and the easiest to get wrong: a
    missing WHERE must mean *every row*, never "no predicate, so nothing to count"."""
    out = resolver.resolve("DELETE FROM users", CTX)
    assert out.magnitude == 1_203


def test_it_counts_an_update_too(resolver: RowsResolver) -> None:
    out = resolver.resolve("UPDATE users SET note = 'z' WHERE org = 'acme'", CTX)
    assert out.magnitude == 1_200


def test_the_count_is_never_exact_because_cascades_are_invisible(resolver: RowsResolver) -> None:
    """The claim NC-10 is narrowed to. `ON DELETE CASCADE` fan-out can only add rows, so the truth
    is at or above the count — sound to block on, never sound to allow on."""
    out = resolver.resolve("DELETE FROM users WHERE org = 'acme'", CTX)

    assert out.direction is Direction.LOWER_BOUND
    assert may_block(out.direction)
    assert not may_allow(out.direction)
    assert "CASCADE" in str(out.evidence["excludes"])


def test_a_genuinely_small_delete_still_cannot_claim_to_be_small(resolver: RowsResolver) -> None:
    """The cost of the LOWER_BOUND position, stated rather than hidden: three rows plus an unknown
    cascade is still an unknown, so this routes through `on_unbounded` instead of sailing past a
    ceiling. Over-caution in the safe direction is the trade NC-10 forces."""
    out = resolver.resolve("DELETE FROM users WHERE org = 'other'", CTX)

    assert out.magnitude == 3
    assert not may_allow(out.direction)


# ---------------------------------------------------------------------------- refusals


def test_a_where_inside_a_string_literal_is_not_a_predicate(resolver: RowsResolver) -> None:
    """The case that makes the hand-written scanner worth having.

    A regex looking for WHERE finds the one inside the quoted value, counts `you like'` as the
    predicate, and either errors or — far worse — counts something real and wrong. The correct
    answer is that this statement has no WHERE clause at all, so it touches every row.
    """
    parsed = parse_statement("UPDATE users SET note = 'delete where you like'")

    assert parsed is not None
    assert parsed.predicate is None, "the WHERE was inside a string literal"
    out = resolver.resolve("UPDATE users SET note = 'delete where you like'", CTX)
    assert out.magnitude == 1_203, "no WHERE clause means every row"


def test_a_where_inside_a_literal_does_not_hide_a_real_predicate() -> None:
    parsed = parse_statement("UPDATE users SET note = 'where' WHERE org = 'acme'")
    assert parsed is not None
    assert parsed.predicate == "org = 'acme'"


@pytest.mark.parametrize(
    ("sql", "predicate"),
    [
        # The regression that made this table exist. An earlier scanner failed to step past a
        # closing quote, so the quote *closing* a literal was read as opening the next one — which
        # inverts which halves of the statement count as quoted and hides every later WHERE. Both
        # of these came back with no predicate, i.e. "the whole table", silently.
        ("UPDATE users SET note = 'it''s where' WHERE id = 1", "id = 1"),
        ("UPDATE users SET a = 'x', b = 'y' WHERE org = 'acme'", "org = 'acme'"),
        # A semicolon inside a literal is not a statement separator.
        ("DELETE FROM users WHERE note = ';'", "note = ';'"),
    ],
)
def test_quote_handling_does_not_lose_the_predicate(sql: str, predicate: str) -> None:
    parsed = parse_statement(sql)
    assert parsed is not None, sql
    assert parsed.predicate == predicate


@pytest.mark.parametrize(
    "sql",
    [
        # `WHEREVER = 1` is five matching letters and not the keyword. Read as WHERE, the predicate
        # becomes `VER = 1` — which can name a real column and return a real, wrong number.
        "DELETE FROM users WHEREVER = 1",
        # Unterminated literal: whatever predicate we extracted is not the one the database sees.
        "DELETE FROM users WHERE note = 'unterminated",
    ],
)
def test_near_misses_that_would_produce_a_wrong_count_are_declined(sql: str) -> None:
    assert parse_statement(sql) is None


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM users WHERE id = 1; DROP TABLE users",
        "DELETE FROM users WHERE id = 1 -- and everything else",
        "DELETE FROM users WHERE id = 1 /* hidden */",
        "DELETE FROM orders USING users WHERE orders.uid = users.id",
        "UPDATE users SET note = 'x' FROM orders WHERE orders.uid = users.id",
        "DELETE FROM users JOIN orders ON users.id = orders.uid",
        "TRUNCATE TABLE users",
        "DROP TABLE users",
        "SELECT * FROM users",
        "",
        "   ",
        "DELETE FROM (SELECT * FROM users)",
    ],
)
def test_anything_not_certainly_understood_is_declined(sql: str) -> None:
    assert parse_statement(sql) is None, f"must not claim to understand: {sql!r}"


def test_a_declined_statement_resolves_unresolved_not_zero(resolver: RowsResolver) -> None:
    """The whole point. A statement we cannot read must route through `on_unresolved`; resolving it
    to 0 would make every unparseable statement the safest-looking call in the system."""
    out = resolver.resolve("TRUNCATE TABLE users", CTX)

    assert out.state is ResolutionState.UNRESOLVED
    assert out.magnitude is None
    assert out.evidence["reason"] == "statement_not_recognised"


def test_a_predicate_cannot_escape_the_count_statement(db: sqlite3.Connection) -> None:
    """`DbapiCountRunner` interpolates the predicate, so this is the test that keeps that honest.

    A statement carrying `;` never reaches the runner, which is why interpolation is survivable —
    the predicate cannot terminate the SELECT and start something else. Asserted by checking the
    table is still there afterwards, not just that the parse returned None.
    """
    resolver = RowsResolver(DbapiCountRunner(db))
    out = resolver.resolve("DELETE FROM users WHERE id = 1; DROP TABLE users", CTX)

    assert out.state is ResolutionState.UNRESOLVED
    assert db.execute("select count(*) from users").fetchone()[0] == 1_203


def test_a_count_that_fails_is_unresolved(db: sqlite3.Connection) -> None:
    """A predicate the database rejects, or a table that is not there. Not zero rows."""
    resolver = RowsResolver(DbapiCountRunner(db))
    out = resolver.resolve("DELETE FROM missing_table WHERE id = 1", CTX)

    assert out.state is ResolutionState.UNRESOLVED
    assert out.evidence["reason"] == "count_failed"


def test_counting_does_not_execute_the_statement(db: sqlite3.Connection) -> None:
    """Sizing a DELETE must not perform it. Obvious, and worth a test precisely because the
    resolver's job is to compose SQL from a destructive statement."""
    RowsResolver(DbapiCountRunner(db)).resolve("DELETE FROM users", CTX)
    assert db.execute("select count(*) from users").fetchone()[0] == 1_203


# ---------------------------------------------------------------------------- shapes it accepts


@pytest.mark.parametrize(
    ("sql", "table"),
    [
        ("delete from users where id = 1", "users"),
        ("DELETE   FROM   public.users   WHERE id = 1", "public.users"),
        ('DELETE FROM "my users" WHERE id = 1', '"my users"'),
        ("DELETE FROM `users` WHERE id = 1", "`users`"),
        ("UPDATE public.users SET a = 1 WHERE id = 1", "public.users"),
        ("DELETE FROM users WHERE id = 1;", "users"),
    ],
)
def test_the_shapes_it_does_accept(sql: str, table: str) -> None:
    parsed = parse_statement(sql)
    assert parsed is not None, sql
    assert parsed.table == table


def test_a_subquery_predicate_is_passed_through_verbatim() -> None:
    """The predicate is handed to `count(*)` unchanged, so anything valid in a WHERE clause works.
    It is the statement *shape* that must be certain, not the predicate's contents."""
    parsed = parse_statement("DELETE FROM users WHERE id IN (SELECT uid FROM orders)")
    assert parsed is not None
    assert parsed.predicate == "id IN (SELECT uid FROM orders)"


def test_reachable_max_refuses_to_invent_a_bound(resolver: RowsResolver) -> None:
    out = resolver.reachable_max(CTX)
    assert out.state is ResolutionState.UNRESOLVED
    assert out.evidence["reason"] == "no_reachable_hint_declared"
