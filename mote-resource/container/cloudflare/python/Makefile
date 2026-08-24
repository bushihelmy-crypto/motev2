.PHONY: install format lint typecheck test static security worker-build package-build package-check check dev deploy

install:
	uv sync --locked
	pnpm install --frozen-lockfile

format:
	uv run ruff check --fix src tests
	uv run ruff format src tests

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

typecheck:
	uv run pyright

test:
	uv run pytest --cov=mote_container_cloudflare --cov-report=term-missing

static: lint typecheck

security:
	uv run pip-audit --strict .

worker-build:
	uv run pywrangler deploy --dry-run --outdir worker-dist

package-build:
	uv run python -m build --no-isolation

package-check: package-build
	uv run python -m twine check dist/*.whl dist/*.tar.gz

check: static test package-check worker-build

dev:
	uv run pywrangler dev

deploy:
	uv run pywrangler deploy
