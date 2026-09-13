# Testing and quality gates

The default `make check` gate is deterministic and does not require external
providers or credentials.

- `cargo test --lib` runs crate-local unit tests.
- `cargo test --test package --test integration` runs external package and
  boundary smoke tests.
- `cargo test --test architecture` verifies ownership, layout, documentation,
  and forbidden dependency shapes.
- `cargo test --test complexity` compares production metrics with the checked-
  in ratchet under `quality/complexity-baseline.json`.
- Formatting, Clippy, rustdoc, Cargo metadata, packaging, and license metadata
  are part of the quality gate.
- `make security` adds cargo-deny and repository secret scanning.

The complexity baseline is exact. Any intentional increase requires an
architecture review and a baseline update in the same change; a reduction must
also lower the baseline so it cannot regress silently.
