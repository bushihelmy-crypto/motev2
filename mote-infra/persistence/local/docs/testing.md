# Testing and quality gates

The default `make check` gate is deterministic and does not require a database,
RPC listener, external provider, or credential.

- `cargo test --lib` runs crate-local unit tests.
- `cargo test --test package --test integration` runs package and boundary
  smoke tests.
- `cargo test --test architecture` checks ownership documentation, package
  hygiene, and forbidden dependency shapes without freezing the provisional
  durable/protocol/persistenced layout.
- `cargo test --test complexity` compares production metrics with the exact
  ratchet in `quality/complexity-baseline.json`.
- Formatting, Clippy, rustdoc, Cargo metadata, packaging, toolchain, and
  license checks are part of the quality gate.
- `make security` adds cargo-deny and repository secret scanning.

The complexity baseline is exact. An intentional increase requires an owner
review and a baseline update in the same change; a reduction must also lower
the baseline so it cannot regress silently.
