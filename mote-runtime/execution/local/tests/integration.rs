//! Package-level integration checks for the executable and ownership docs.

use std::fs;
use std::path::{Path, PathBuf};

fn package_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn read(relative: impl AsRef<Path>) -> String {
    let path = package_root().join(relative);
    fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", path.display()))
}

#[test]
fn binary_entrypoint_is_present_and_intentionally_small() {
    let source = read("src/main.rs");
    assert!(source.contains("fn main()"));
    assert!(source.contains("Process entry point"));
}

#[test]
fn package_declares_one_binary_and_one_library_target() {
    let manifest = read("Cargo.toml");
    assert_eq!(manifest.matches("[[bin]]").count(), 1);
    assert_eq!(manifest.matches("[lib]").count(), 1);
}

#[test]
fn architecture_docs_keep_transport_and_provider_ownership_external() {
    let architecture = read("docs/architecture.md");
    assert!(architecture.contains("Kernel submits every tool execution to"));
    assert!(architecture.contains("route config"));
    assert!(architecture.contains("mote-infra/invocation"));
    assert!(architecture.contains("execution/local invocation boundary"));
    assert!(architecture.contains("execution/remote"));
    assert!(architecture.contains("endpoint resolution"));
    assert!(architecture.contains("Skill read handler"));
    assert!(architecture.contains("Their schemas"));
    assert!(architecture.contains("Hub registry"));
}

#[test]
fn package_is_not_a_workspace_or_nested_repository() {
    let manifest = read("Cargo.toml");
    assert!(!manifest.contains("[workspace"));
    assert!(!package_root().join(".git").exists());
}
