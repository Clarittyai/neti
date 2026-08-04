# neti — common tasks

default:
    @just --list

install:
    uv venv --python 3.12
    uv pip install -e '.[dev,cli,graph,mcp,console,sdks,sdks-extended,storage,database]'

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

# The two coverage questions, in one command, offline.
#
#   Does every door reach the same verdict?   eleven seams x five resolver families, one table.
#   Do we detect what an agent actually runs?  170 real tool schemas, and the judgement on each.
#
# Both used to be answerable only by reading the code and believing it. `neti init` gated 0 of 160
# real MCP tools for the life of the project and nothing said so, because every fixture in the suite
# was a tool somebody here wrote to be gateable.
conformance:
    NETI_REQUIRE_SDKS=1 uv run pytest -q tests/e2e/test_seam_equivalence.py tests/corpus tests/e2e/test_proof.py
    uv run neti prove -c examples/entra.yaml -r out/proof.ndjson
    uv run neti score -c examples/entra.yaml --field eval/results/mcp_coverage.json

# Byte-equality of the record across fresh interpreters with different hash seeds. This is the
# determinism claim; one process proves nothing.
determinism:
    PYTHONHASHSEED=0 uv run pytest -q tests/property/test_determinism.py
    PYTHONHASHSEED=1 uv run pytest -q tests/property/test_determinism.py
    PYTHONHASHSEED=random uv run pytest -q tests/property/test_determinism.py

# M12: can a model do the detection job the rule table cannot?
#
# Arm A is the go/no-go and needs no hand-labelling. It feeds back the 31 tools the rule table
# already gates, with the answer withheld, and scores what comes back against the committed key.
# If a model cannot recover gates the rules already make, nothing downstream is interpretable.
#
# Your key, your account, your machine. neti never proxies this.
assist provider="anthropic":
    uv run python -m eval.harness.assist --provider {{provider}}

# The offline scorecard: incident replay, friction, blind spots, and what is still unmeasured.
score:
    uv run neti score -c examples/entra.yaml

# The README's images, rendered from the golden transcripts.
#
# Not screenshots. Each SVG is a pure function of a transcript `tests/golden` already pins byte for
# byte, so a change to what the product says fails the golden suite, and updating the transcript
# fails `test_media_is_current` until this is re-run. A picture and the program cannot disagree.
media:
    uv run python tools/make_media.py

# The landing page, built from site/page.html with those same images inlined.
#
# docs/index.html is what GitHub Pages serves; build/page.html is the body alone, for previewing
# before anything is committed. Both come from one source, because two copies of a landing page kept
# in sync by hand is how a landing page starts lying.
site: media
    uv run python tools/make_site.py

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
#
# Leaves `eval/results/live_verification.json` behind, which `neti score` reads for M11 — so a
# resolver the card *claims* is live-verified and a resolver a run actually verified stop being the
# same thing. A skipped module records as skipped, never as passed.
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

# Re-derive the offline detection corpus from whatever `just field` last measured. The corpus is
# what makes coverage a build failure rather than a number nobody recomputes, so run this after
# every field survey and read the diff — every line of decisions.json is a judgement.
corpus-refresh:
    uv run python -m eval.surveys.mcp_coverage
    uv run python -m tests.corpus.refresh

# M7. Puts a real model in the loop, denies it, and records what it does next — the last claim in
# the project resting on plausibility rather than evidence. Needs ANTHROPIC_API_KEY and costs
# tokens; the tools resolve against the synthetic tenant, so nothing real is touched.
m7 RUNS="3":
    uv run python -m eval.harness.m7 --runs {{RUNS}}

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
