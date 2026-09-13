//! Unit-level boundary smoke tests.
//!
//! Runtime behavior is deliberately not implemented yet. These tests keep
//! the public crate surface useful while the first conformance-backed slice is
//! designed.

use mote_runtime_execution_local::VERSION;

#[test]
fn version_is_non_empty_and_static() {
    assert!(!VERSION.is_empty());
    assert!(VERSION.as_bytes().iter().all(u8::is_ascii));
}

#[test]
fn version_does_not_contain_transport_or_provider_state() {
    assert!(!VERSION.contains("socket"));
    assert!(!VERSION.contains("mcp"));
    assert!(!VERSION.contains("skill"));
}
