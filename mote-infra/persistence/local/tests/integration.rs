//! Package-level integration checks for metadata and ownership documents.

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
fn package_declares_a_single_library_target() {
    let manifest = read("Cargo.toml");
    assert_eq!(manifest.matches("[lib]").count(), 1);
}

#[test]
fn quality_assets_are_part_of_the_package() {
    let manifest = read("Cargo.toml");
    for included in ["docs/**", "quality/**", "scripts/**", "tests/**"] {
        assert!(
            manifest.contains(&format!("\"{included}\"")),
            "package include list is missing {included}"
        );
    }
}

#[test]
fn architecture_docs_preserve_external_owner_boundaries() {
    let architecture = read("docs/architecture.md");
    assert!(architecture.contains("Kernel owns Agent flow semantics"));
    assert!(architecture.contains("`Commit` Port contract"));
    assert!(architecture.contains("mote-infra/invocation"));
    assert!(architecture.contains("Container"));
    assert!(architecture.contains("conformance/"));
}

#[test]
fn provisional_layout_is_not_documented_as_a_public_contract() {
    let architecture = read("docs/architecture.md");
    assert!(architecture.contains("provisional responsibility areas"));
    assert!(architecture.contains("not public package boundaries"));

    let readme = read("README.md");
    assert!(readme.contains("not stable package contracts"));
}

#[test]
fn package_is_not_a_cargo_workspace() {
    let manifest = read("Cargo.toml");
    assert!(!manifest.contains("[workspace"));
}
