use mote_infra::VERSION;

#[test]
fn package_version_matches_cargo_metadata() {
    assert_eq!(VERSION, env!("CARGO_PKG_VERSION"));
}
