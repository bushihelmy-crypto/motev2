//! Local tool execution runtime for Mote.
//!
//! The crate owns local execution admission, route policy, and tool behavior
//! after Invocation delivers a request. Unix socket framing, endpoint
//! resolution, retry, and remote transport are supplied by Mote Invocation
//! Infrastructure. Cross-language request and result shapes are implemented
//! here only after they are fixed by `conformance/`.

pub mod api;
pub mod ports;

mod admission;
mod catalog;
mod dispatch;
mod invocation;
mod outcome;
mod process;
mod routing;
mod tools;
mod workspace;

/// The package version supplied by Cargo metadata.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[cfg(test)]
mod tests {
    #[test]
    fn version_is_static_package_metadata() {
        assert_eq!(super::VERSION, env!("CARGO_PKG_VERSION"));
    }
}
