# Mote Local Infrastructure Persistence

Mote Local Infrastructure Persistence is the Rust implementation of Mote's reliability substrate for local and host-native deployments. It will provide durable state, atomic commit, operation receipts, coordination, reliable execution attempts, workspace state, and storage adapters without interpreting Agent flow semantics.

Container and persistence choices are independent. Kernel Port configuration may select this backend for a local, Docker, or Cloudflare-hosted Agent; the selected Container neither owns nor chooses it.

The project is in its bootstrap phase. No public commit, wire, RPC, or daemon startup API has been fixed yet.

## Provisional source layout

    src/
    ├── durable/   Internal reliability semantics and storage ports
    ├── protocol/  Strict mapping between conformance-owned wire values and Rust values
    └── persistenced/    Configuration and standalone service composition

    tests/
    ├── package.rs       External package-import smoke test
    ├── unit.rs          Crate-level smoke tests
    ├── integration.rs   Metadata and ownership-document checks
    ├── architecture.rs  Non-speculative ownership and hygiene checks
    └── complexity.rs    Exact production-code complexity ratchet

These directories are candidate responsibilities used to advance the design discussion, not stable package contracts. Until the first consumer-driven storage vertical slice exists, architecture tests do not freeze this layout or its dependency direction, and external consumers must not rely on these modules as a committed API.

## Engineering baseline

This package starts without database or RPC dependencies. Concrete dependencies will be added with the first vertical slice and its conformance cases.

The project setup follows established Rust practices for a pinned toolchain, formatting, Clippy, focused tests, documentation, packaging, and dependency policy. Turso's in-memory IO, reproducible seeds, deterministic simulation, and fault-injection discipline are useful future references; its SQL storage traits, module layout, and type-erasure choices do not define Mote interfaces.

## Development

The project pins Rust 1.85.0 and declares Rust 1.85 as its MSRV in `Cargo.toml`. Rustup uses `rust-toolchain.toml` when entering this directory.

    make format
    make check

`make check` runs the same deterministic phases as Local Execution: structure,
toolchain, formatting, Clippy, architecture, complexity, documentation, Cargo
hygiene, licensing, focused tests, build, and package verification. See
[`docs/testing.md`](docs/testing.md) for the individual targets.

Install cargo-deny and detect-secrets before running the dependency, license,
source, advisory, and secret checks:

    cargo install cargo-deny --version 0.19.7 --locked
    python -m pip install detect-secrets==1.5.0
    make security

The security target also requires `detect-secrets==1.5.0` and scans this
package against the monorepo baseline.

The root conformance directory owns cross-language and durable protocol contracts. Protocol work in this package must be accompanied by the corresponding conformance schema and cases.

See the [Mote platform architecture](../../../docs/mote-platform-architecture.zh-CN.md) for the wider vision and owner boundaries.

## Status

Pre-alpha. The source tree is an adjustable design scaffold plus an engineering baseline; internal boundaries are not frozen.

## License

Apache License 2.0. See LICENSE.

Chinese documentation is available in README.zh-CN.md.
