.PHONY: install format lint test typecheck security build package-check check

install:
	python -m pip install -e '.[dev]'

format:
	python -m ruff check --fix src tests
	python -m ruff format src tests

lint:
	python -m ruff check src tests
	python -m ruff format --check src tests

test:
	python -m pytest --cov=mote_kernel --cov-report=term-missing

typecheck:
	pyright

security:
	cd .. && pre-commit run detect-secrets --all-files
	python -m pip_audit --strict .

build:
	python -m build --no-isolation

package-check: build
	python -m twine check dist/*

check: lint typecheck test package-check
