.PHONY: test lint build migrate test-unit test-integration test-stress rust-build docker-up docker-down clean

# ── Testing ──────────────────────────────────────────────────────────────────
test: test-unit

test-unit:
	python -m pytest tests/unit -v --tb=short

test-integration:
	python -m pytest tests/integration -v --tb=short

test-stress:
	python -m pytest tests/stress -v --tb=short

test-all:
	python -m pytest tests/ -v --tb=short

# ── Linting & Type Checking ──────────────────────────────────────────────────
lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

typecheck:
	mypy src/

import-check:
	lint-imports

# ── Build ────────────────────────────────────────────────────────────────────
build: rust-build
	pip install -e .

rust-build:
	cd rust/py-bindings && maturin develop --release

# ── Database Migrations ──────────────────────────────────────────────────────
migrate:
	alembic -c migrations/pg/alembic.ini upgrade head

migrate-create:
	alembic -c migrations/pg/alembic.ini revision --autogenerate -m "$(MSG)"

# ── Docker ───────────────────────────────────────────────────────────────────
docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-clean:
	docker compose down -v

# ── Utilities ────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	cargo clean 2>/dev/null || true
