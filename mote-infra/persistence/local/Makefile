SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := check

CARGO ?= cargo
MANIFEST ?= $(CURDIR)/Cargo.toml
TARGET_DIR ?= $(CURDIR)/target

export CARGO_TERM_COLOR ?= always
export CARGO_TARGET_DIR ?= $(TARGET_DIR)

.PHONY: all help check quality format fmt-check lint test test-unit test-integration
.PHONY: architecture complexity complexity-report docs build package-check
.PHONY: toolchain-check cargo-hygiene structure-check security secret-scan
.PHONY: license-check clean

all: check

help:
	@printf '%s\n' 'Mote Local Infrastructure Persistence commands:'
	@printf '%s\n' '  make check             complete deterministic quality gate'
	@printf '%s\n' '  make test-unit         crate-local unit tests'
	@printf '%s\n' '  make test-integration  external package and integration tests'
	@printf '%s\n' '  make architecture      ownership and package-layout checks'
	@printf '%s\n' '  make complexity        exact production complexity ratchet'
	@printf '%s\n' '  make security          cargo-deny and secret scanning'
	@printf '%s\n' '  make build             reproducible debug build'

check: quality test-unit test-integration build package-check

quality: structure-check toolchain-check fmt-check lint architecture complexity docs cargo-hygiene license-check

structure-check:
	@bash scripts/check-structure.sh

toolchain-check:
	@bash scripts/check-toolchain.sh

format:
	@$(CARGO) fmt --manifest-path $(MANIFEST) --all

fmt-check:
	@$(CARGO) fmt --manifest-path $(MANIFEST) --all -- --check

lint:
	@$(CARGO) clippy --manifest-path $(MANIFEST) --locked --all-targets --all-features -- -D warnings

test: test-unit test-integration

test-unit:
	@$(CARGO) test --manifest-path $(MANIFEST) --locked --lib --all-features
	@$(CARGO) test --manifest-path $(MANIFEST) --locked --test unit --all-features

test-integration:
	@$(CARGO) test --manifest-path $(MANIFEST) --locked --test package --test integration

architecture:
	@$(CARGO) test --manifest-path $(MANIFEST) --locked --test architecture

complexity:
	@$(CARGO) test --manifest-path $(MANIFEST) --locked --test complexity

complexity-report:
	@MOTE_COMPLEXITY_REPORT=1 $(CARGO) test --manifest-path $(MANIFEST) --locked --test complexity -- --nocapture

docs:
	@RUSTDOCFLAGS='--deny warnings' $(CARGO) doc --manifest-path $(MANIFEST) --locked --all-features --no-deps

build:
	@$(CARGO) build --manifest-path $(MANIFEST) --locked --all-targets --all-features

package-check:
	@$(CARGO) package --manifest-path $(MANIFEST) --locked --allow-dirty

cargo-hygiene:
	@bash scripts/check-cargo-hygiene.sh

security: secret-scan
	@$(CARGO) deny --manifest-path $(MANIFEST) --locked check

secret-scan:
	@bash scripts/check-secrets.sh

license-check:
	@bash scripts/check-license.sh

clean:
	@$(CARGO) clean --manifest-path $(MANIFEST)
