.PHONY: install format lint typecheck test static security package-build package-check check

install:
	uv sync --locked

format:
	uv run ruff check --fix src tests
	uv run ruff format src tests

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

typecheck:
	uv run pyright

test:
	uv run pytest --cov=mote_infra_persistence_cloudflare --cov-report=term-missing

static: lint typecheck

security:
	uv run pip-audit --strict .

package-build:
	uv run python -m build --no-isolation

package-check: package-build
	uv run python -m twine check dist/*.whl dist/*.tar.gz

check: static test package-check
