//! Reliable state, execution, and workspace mechanisms for Mote.
//!
//! The package currently contains private candidate responsibility areas while
//! the first consumer-driven vertical slice is designed. Their names and
//! dependency direction are not yet public contracts. Agent flow semantics
//! remain owned by the Python Kernel, and cross-language contracts remain
//! owned by the monorepo root conformance directory.

mod durable;
mod infrad;
mod protocol;

/// The package version supplied by Cargo metadata.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
