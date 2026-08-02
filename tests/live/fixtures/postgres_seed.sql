-- Fixture for tests/live/test_postgres_live.py. Counts here are asserted exactly, so changing a
-- number means changing the test.
--
-- The shape that matters is the cascade. `db.rows` counts with `select count(*)` against the
-- statement's own table, and SCOPE.md NC-10 says the fan-out through `ON DELETE CASCADE` is
-- invisible to it — which is why every result is a LOWER_BOUND rather than an EXACT. Offline that
-- claim is asserted against sqlite and a hand-built fixture; here it is asserted against a real
-- Postgres that will really delete 600 rows when asked to delete 100.

DROP TABLE IF EXISTS children;
DROP TABLE IF EXISTS parents;

CREATE TABLE parents (
    id   integer PRIMARY KEY,
    tier text NOT NULL
);

CREATE TABLE children (
    id        integer PRIMARY KEY,
    parent_id integer NOT NULL REFERENCES parents (id) ON DELETE CASCADE
);

-- 100 parents: 40 in tier 'a', 60 in tier 'b'.
INSERT INTO parents (id, tier)
SELECT i, CASE WHEN i <= 40 THEN 'a' ELSE 'b' END
FROM generate_series(1, 100) AS i;

-- 5 children each, so deleting all 100 parents really removes 600 rows.
INSERT INTO children (id, parent_id)
SELECT i, ((i - 1) / 5) + 1
FROM generate_series(1, 500) AS i;

-- The grant `db.rows`'s own docstring asks for: "Point it at a read-only user." The recogniser
-- refuses anything that could escape the SELECT, and this is the check that does not depend on the
-- recogniser being right.
-- Idempotent: re-seeding must not fail on a fresh container (no role to drop) or on a warm one
-- (a role still owning grants). `DROP OWNED BY` errors on a role that does not exist, so it is
-- guarded rather than run unconditionally.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'neti_ro') THEN
        EXECUTE 'DROP OWNED BY neti_ro';
        EXECUTE 'DROP ROLE neti_ro';
    END IF;
END
$$;

CREATE ROLE neti_ro LOGIN PASSWORD 'neti_ro';
GRANT CONNECT ON DATABASE neti TO neti_ro;
GRANT USAGE ON SCHEMA public TO neti_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO neti_ro;
