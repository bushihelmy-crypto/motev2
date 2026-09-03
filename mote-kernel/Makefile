.PHONY: install format lint complexity complexity-ratchet complexity-report test typecheck security build package-check check

install:
	python -m pip install -e '.[dev]'

format:
	python -m ruff check --fix src tests example
	python -m ruff format src tests example

lint:
	python -m ruff check src tests example
	python -m ruff format --check src tests example

complexity:
	python -m tests.architecture.complexity_rules --check-health

complexity-ratchet:
	python -m pytest -q tests/architecture/test_complexity_gate.py tests/architecture/test_semantic_index.py

complexity-report:
	python -m tests.architecture.complexity_rules

test:
	python -m pytest --cov=mote_kernel --cov-report=term-missing

# Pyright's resolver needs an explicit environment; derive it from this `python` without
# committing a developer-specific path into the project configuration.
typecheck:
	@pyright_config="$$(mktemp "$(CURDIR)/.pyrightconfig.XXXXXX.json")"; \
	trap 'rm -f "$$pyright_config"' EXIT; \
	python -B -c 'import json, pathlib, sys; prefix = pathlib.Path(sys.prefix); print(json.dumps({"extends": "pyproject.toml", "venvPath": str(prefix.parent), "venv": prefix.name}))' > "$$pyright_config"; \
	python -B -m pyright -p "$$pyright_config"

security:
	cd .. && pre-commit run detect-secrets --all-files
	python -m pip_audit --strict .

build:
	python -m build --no-isolation

package-check: build
	python -m twine check dist/*

check: lint typecheck complexity-ratchet complexity test package-check
