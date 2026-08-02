# neti — common tasks

default:
    @just --list

install:
    uv venv --python 3.12
    uv pip install -e '.[dev,cli,graph,mcp,console,sdks,storage,database]'

test:
    uv run pytest -q

# What CI runs. `NETI_REQUIRE_SDKS` turns "the SDK extra is not installed" from a skipped test into
# a failing one — the three agent runtimes most people reach for were silently untested for a whole
# release, because a skip reads exactly like a pass in a `-q` summary.
test-all:
    NETI_REQUIRE_SDKS=1 uv run pytest -q

# The load-bearing suite: determinism, monotonicity, direction soundness, purity.
prop:
    uv run pytest -q tests/property

# neti's own overhead with the network mocked. The real budget belongs to Graph — see `measure`.
bench:
    uv run pytest -q tests/bench

types:
    uv run mypy

lint:
    uv run ruff check
    uv run ruff format --check

fmt:
    uv run ruff format
    uv run ruff check --fix

check: lint types test

# Byte-equality of the record across fresh interpreters with different hash seeds. This is the
# determinism claim; one process proves nothing.
determinism:
    PYTHONHASHSEED=0 uv run pytest -q tests/property/test_determinism.py
    PYTHONHASHSEED=1 uv run pytest -q tests/property/test_determinism.py
    PYTHONHASHSEED=random uv run pytest -q tests/property/test_determinism.py

# The offline scorecard: incident replay, friction, blind spots, and what is still unmeasured.
score:
    uv run neti score -c examples/entra.yaml

# ---------------------------------------------------------------------------- the live tier
#
# `db.rows`, `storage.objects` and `terraform.destroy` all shipped without ever having touched a
# real provider: the first two were tested against a stdlib sqlite and a mock lister, and the third
# against plan JSON we wrote ourselves. Every defect this tier has found was invisible offline, and
# it needs no cloud account — Postgres and MinIO in Docker, and the `null` provider for Terraform.
#
# Credentials below are throwaway fixtures for local containers. Nothing here reaches a real
# account, and `just live-down` removes both containers.

live-up:
    docker rm -f neti-live-pg neti-live-minio 2>/dev/null || true
    docker run -d --name neti-live-pg -e POSTGRES_PASSWORD=neti -e POSTGRES_DB=neti \
        -p 55432:5432 postgres:16
    docker run -d --name neti-live-minio -p 59000:9000 \
        -e MINIO_ROOT_USER=netiminio -e MINIO_ROOT_PASSWORD=netiminio123 \
        minio/minio server /data
    until docker exec neti-live-pg pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
    docker exec -i neti-live-pg psql -U postgres -d neti -v ON_ERROR_STOP=1 \
        < tests/live/fixtures/postgres_seed.sql
    until curl -sf http://127.0.0.1:59000/minio/health/live >/dev/null; do sleep 1; done
    AWS_ACCESS_KEY_ID=netiminio AWS_SECRET_ACCESS_KEY=netiminio123 AWS_DEFAULT_REGION=us-east-1 \
        aws --endpoint-url http://127.0.0.1:59000 s3 mb s3://neti-live 2>/dev/null || true
    AWS_ACCESS_KEY_ID=netiminio AWS_SECRET_ACCESS_KEY=netiminio123 AWS_DEFAULT_REGION=us-east-1 \
        uv run python tests/live/fixtures/seed_minio.py

live-down:
    docker rm -f neti-live-pg neti-live-minio 2>/dev/null || true

# Everything the live tier can reach locally. GitHub needs a token and is included when `gh` has
# one; the Entra half stays skipped until somebody has a tenant (scorecard M2, R2).
live:
    NETI_DATABASE_URL=postgresql://neti_ro:neti_ro@127.0.0.1:55432/neti \
    AWS_ACCESS_KEY_ID=netiminio AWS_SECRET_ACCESS_KEY=netiminio123 \
    AWS_DEFAULT_REGION=us-east-1 AWS_ENDPOINT_URL=http://127.0.0.1:59000 \
    NETI_S3_BUCKET=neti-live NETI_LIVE_TERRAFORM=1 \
    NETI_GITHUB_TOKEN="${NETI_GITHUB_TOKEN:-$(gh auth token 2>/dev/null || true)}" \
        uv run pytest tests/live -q

# ---------------------------------------------------------------------------- field trials
#
# Real agents and real providers. Never CI: non-deterministic, needs the network, some of it costs
# tokens. See eval/README.md.
field:
    uv run python -m eval.surveys.mcp_coverage --markdown

# Requires a live tenant. This is the measurement every latency figure in the plan is waiting on.
measure GROUP_SMALL GROUP_LARGE:
    uv run neti measure -g {{GROUP_SMALL}} -g {{GROUP_LARGE}}

# Build the console and copy the export into the Python package, so the wheel ships a UI.
console-sync:
    cd web && npm run build
    rm -rf src/neti/console
    cp -R web/out src/neti/console

# Everything a release needs: the console, then the wheel.
dist: console-sync
    uv build
