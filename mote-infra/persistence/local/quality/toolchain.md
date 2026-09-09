# Rust toolchain policy

The package pins Rust `1.85.0` in `rust-toolchain.toml` and declares Rust
`1.85` as its MSRV in `Cargo.toml`.

The baseline CI job runs the pinned toolchain. A compatibility job also runs
the complete test set on current stable Rust. Updating either version requires
reviewing the lockfile, quality output, and this document together.

Commands use the package manifest directly. This is a standalone Cargo
package; it must not add a nested workspace or a second manifest.
