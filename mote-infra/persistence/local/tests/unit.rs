//! Unit-level package smoke tests.
//!
//! Persistence behavior is deliberately not implemented until the first
//! consumer-driven, conformance-backed storage slice is accepted.

use mote_infra_persistence_local::VERSION;

#[test]
fn version_is_non_empty_and_static() {
    assert!(!VERSION.is_empty());
    assert!(VERSION.as_bytes().iter().all(u8::is_ascii));
}

#[test]
fn version_contains_no_backend_or_transport_configuration() {
    assert!(!VERSION.contains("sqlite"));
    assert!(!VERSION.contains("rpc"));
    assert!(!VERSION.contains("cloudflare"));
}
