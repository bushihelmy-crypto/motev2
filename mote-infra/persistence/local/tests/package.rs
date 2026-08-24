//! External smoke tests for the public crate surface.

use mote_infra_persistence_local::VERSION;

#[test]
fn package_version_matches_cargo_metadata() {
    assert_eq!(VERSION, env!("CARGO_PKG_VERSION"));
}
