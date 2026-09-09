//! External smoke tests for the published package surface.

use mote_runtime_execution_local::VERSION;

#[test]
fn package_version_matches_cargo_metadata() {
    assert_eq!(VERSION, env!("CARGO_PKG_VERSION"));
}

#[test]
fn package_name_is_stable() {
    assert_eq!(env!("CARGO_PKG_NAME"), "mote-runtime-execution-local");
}

#[test]
fn package_version_is_semver_shaped() {
    let mut components = VERSION.split('.');
    assert!(
        components
            .next()
            .is_some_and(|value| value.parse::<u64>().is_ok())
    );
    assert!(
        components
            .next()
            .is_some_and(|value| value.parse::<u64>().is_ok())
    );
    assert!(
        components
            .next()
            .is_some_and(|value| value.parse::<u64>().is_ok())
    );
    assert!(components.next().is_none());
}
