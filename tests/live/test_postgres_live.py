"""`db.rows` against a real Postgres. Opt-in.

    just live-up
    NETI_DATABASE_URL=postgresql://neti_ro:neti_ro@127.0.0.1:55432/neti uv run pytest tests/live -q

The offline suite for this resolver is 39 tests and every one of them runs against stdlib sqlite,
which means the `psycopg` branch of `_connect_from_env` — the half an operator actually deploys —
had never executed. Neither had the claim SCOPE.md NC-10 rests on: that `ON DELETE CASCADE` fan-out
is invisible to `select count(*)`, so a count is a floor rather than a total. Against sqlite that is
an argument. Against a database that really will delete six hundred rows when told to delete one
hundred, it is a measurement.

The fixture is `tests/live/fixtures/postgres_seed.sql`; the counts below are asserted exactly, so
the two files change together.

Every statement here is a `SELECT`. `db.rows` never executes the `DELETE` it is sizing, and the
fixture role is granted `SELECT` only — the check that does not depend on the recogniser being
right.
"""

from __future__ import annotations

import os

import pytest

from neti.core.units import Direction, may_allow, may_block
from neti.core.verdict import ResolutionState
from neti.resolvers.base import ResolveContext
from neti.resolvers.database import EnvCountRunner, RowsResolver

DSN = os.environ.get("NETI_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DSN.startswith(("postgres://", "postgresql://")),
    reason="live Postgres check: `just live-up`, then set NETI_DATABASE_URL to the postgres DSN",
)

CTX = ResolveContext()

PARENTS = 100
PARENTS_TIER_A = 40
CHILDREN = 500


@pytest.fixture
def rows() -> RowsResolver:
    """A resolver connecting the way a deployed one does — from the environment, lazily."""
    return RowsResolver(EnvCountRunner())


def test_psycopg_is_installed() -> None:
    """The reason this file exists at all.

    `neti[database]` was in the CI install line and in `just install`, and `psycopg` was absent from
    a working checkout for long enough to matter — because no test needed it. Every assertion below
    is worthless if the import silently falls back to something else, so it is checked first and by
    name.
    """
    import psycopg  # noqa: F401


def test_a_delete_with_no_predicate_counts_the_whole_table(rows: RowsResolver) -> None:
    out = rows.resolve("DELETE FROM parents", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == PARENTS
    assert out.evidence["verb"] == "DELETE"
    assert out.evidence["table"] == "parents"
    assert out.evidence["predicate"] is None
    assert out.evidence["counted_by"] == "select count(*)"


def test_a_predicate_is_applied_rather_than_ignored(rows: RowsResolver) -> None:
    """A resolver that dropped the WHERE clause would still return a plausible number here.

    That is the failure mode worth paying a live test for: `100` instead of `40` is not obviously
    wrong on an inspection, and it is wrong in the permissive direction on a ceiling of 50.
    """
    out = rows.resolve("DELETE FROM parents WHERE tier = 'a'", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == PARENTS_TIER_A
    assert out.evidence["predicate"] == "tier = 'a'"


def test_an_update_is_sized_the_same_way(rows: RowsResolver) -> None:
    out = rows.resolve("UPDATE parents SET tier = 'c' WHERE tier = 'a'", CTX)

    assert out.state is ResolutionState.RESOLVED
    assert out.magnitude == PARENTS_TIER_A
    assert out.evidence["verb"] == "UPDATE"


def test_a_real_cascade_is_invisible_which_is_why_this_is_never_exact(rows: RowsResolver) -> None:
    """NC-10, against a database that really cascades.

    `DELETE FROM parents` removes 100 parent rows and 500 child rows. The resolver reports 100 and
    calls it a LOWER_BOUND. Both halves matter: the number is short of the truth, and the direction
    says so, so the decision procedure can block on it and can never allow on it.
    """
    parents = rows.resolve("DELETE FROM parents", CTX)
    children = rows.resolve("DELETE FROM children", CTX)

    assert parents.magnitude == PARENTS
    assert children.magnitude == CHILDREN
    assert parents.magnitude is not None
    assert children.magnitude is not None
    assert parents.magnitude + children.magnitude > PARENTS, (
        "the fixture must actually cascade, or this test asserts nothing"
    )

    assert parents.direction is Direction.LOWER_BOUND
    assert may_block(parents.direction)
    assert not may_allow(parents.direction)
    assert parents.evidence["excludes"] == "ON DELETE CASCADE fan-out (SCOPE.md NC-10)"


def test_a_missing_table_is_unresolved_not_zero(rows: RowsResolver) -> None:
    """The invariant every resolver shares, against a real server's real error.

    Offline this is asserted against a runner that was told to raise. Here the exception is
    Postgres's own `UndefinedTable`, arriving through psycopg, and it must still land as UNRESOLVED
    with no magnitude — because an empty table and an absent one are opposite situations.
    """
    out = rows.resolve("DELETE FROM no_such_table_xyzzy", CTX)

    assert out.state is ResolutionState.UNRESOLVED
    assert out.magnitude is None
    assert out.evidence["table"] == "no_such_table_xyzzy"
    assert "no_such_table_xyzzy" in out.evidence["error"], (
        "the server's own words, so an operator can tell a typo from a revoked grant"
    )


def test_a_statement_it_does_not_certainly_recognise_is_declined(rows: RowsResolver) -> None:
    """NC-10's other half: reading a statement is a syntactic gate, so it declines far more than it
    accepts. A multi-table delete is exactly the shape that would produce a confident wrong count.
    """
    out = rows.resolve(
        "DELETE FROM parents USING children WHERE parents.id = children.parent_id", CTX
    )

    assert out.state is ResolutionState.UNRESOLVED
    assert out.magnitude is None


def test_the_read_only_grant_holds(rows: RowsResolver) -> None:
    """Belt and braces, asserted rather than assumed.

    `db.rows` composes SQL from an agent's own statement. The recogniser refuses anything that could
    escape the `SELECT`, and the fixture role cannot write even if it did not. If this fixture is
    ever pointed at a privileged role, this fails and says so.
    """
    import psycopg

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("DELETE FROM children WHERE id = 1")
        conn.rollback()

        cur.execute("SELECT count(*) FROM children")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == CHILDREN, "nothing above may have modified the fixture"
