# Mote Local Infra

Mote Local Infra is the Rust implementation of Mote's reliability substrate for local and host-native deployments. It will provide durable state, atomic commit, operation receipts, coordination, reliable execution attempts, workspace state, and storage adapters without interpreting Agent flow semantics.

The project is in its bootstrap phase. No public commit, wire, RPC, or daemon startup API has been fixed yet.

## Provisional source layout

    src/
    ├── durable/   Internal reliability semantics and storage ports
    ├── protocol/  Strict mapping between conformance-owned wire values and Rust values
    └── infrad/    Configuration and standalone service composition

    tests/
    └── package.rs   External package-import smoke test

These directories are candidate responsibilities used to advance the design discussion, not stable package contracts. Until the first consumer-driven storage vertical slice exists, architecture tests do not freeze this layout or its dependency direction, and external consumers must not rely on these modules as a committed API.

## Engineering baseline

This package starts without database or RPC dependencies. Concrete dependencies will be added with the first vertical slice and its conformance cases.

The project setup follows established Rust practices for a pinned toolchain, formatting, Clippy, focused tests, documentation, packaging, and dependency policy. Turso's in-memory IO, reproducible seeds, deterministic simulation, and fault-injection discipline are useful future references; its SQL storage traits, module layout, and type-erasure choices do not define Mote interfaces.

## Development

The project pins Rust 1.85.0 and declares Rust 1.85 as its MSRV in `Cargo.toml`. Rustup uses `rust-toolchain.toml` when entering this directory.

    make format
    make check

Install cargo-deny before running dependency license, source, and advisory checks:

    cargo install cargo-deny --version 0.19.7 --locked
    make security

The root conformance directory owns cross-language and durable protocol contracts. Protocol work in this package must be accompanied by the corresponding conformance schema and cases.

See the [Mote platform architecture](../../docs/mote-platform-architecture.zh-CN.md) for the wider vision and owner boundaries.

## Status

Pre-alpha. The source tree is an adjustable design scaffold plus an engineering baseline; internal boundaries are not frozen.

## License

Apache License 2.0. See LICENSE.

Chinese documentation is available in README.zh-CN.md.
