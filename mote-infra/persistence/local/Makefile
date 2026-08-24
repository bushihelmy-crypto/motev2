.PHONY: format lint test docs build package-check security check

format:
	cargo fmt --all

lint:
	cargo fmt --all --check
	cargo clippy --locked --all-targets --all-features -- --deny warnings

test:
	cargo test --locked --all-targets --all-features

docs:
	RUSTDOCFLAGS="--deny warnings" cargo doc --locked --all-features --no-deps

build:
	cargo build --locked --all-targets --all-features

package-check:
	cargo package --locked --allow-dirty

security:
	cd ../.. && pre-commit run detect-secrets --all-files
	cargo deny check

check: lint test docs build package-check
