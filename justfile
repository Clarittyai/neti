# neti — common tasks

default:
    @just --list

install:
    uv venv --python 3.12
    uv pip install -e '.[dev,cli,graph,mcp]'

test:
    uv run pytest -q

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
