//! Ownership and package-hygiene tests.

use std::fs;
use std::path::{Path, PathBuf};

fn package_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn source_files(root: &Path, output: &mut Vec<PathBuf>) {
    let entries = fs::read_dir(root)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", root.display()));
    for entry in entries {
        let entry = entry.expect("directory entry must be readable");
        let path = entry.path();
        if path.is_dir() {
            source_files(&path, output);
        } else if path.extension().is_some_and(|extension| extension == "rs") {
            output.push(path);
        }
    }
}

#[test]
fn every_production_module_has_owner_documentation() {
    let mut files = Vec::new();
    source_files(&package_root().join("src"), &mut files);
    assert!(!files.is_empty());

    for file in files {
        let source = fs::read_to_string(&file)
            .unwrap_or_else(|error| panic!("cannot read {}: {error}", file.display()));
        let first_line = source.lines().find(|line| !line.trim().is_empty());
        assert!(
            first_line.is_some_and(|line| line.trim_start().starts_with("//!")),
            "{} must begin with //! module documentation",
            file.display()
        );
    }
}

#[test]
fn provisional_layout_remains_explicitly_unfrozen() {
    let architecture = fs::read_to_string(package_root().join("docs/architecture.md"))
        .expect("architecture boundary must be documented");
    assert!(architecture.contains("provisional responsibility areas"));
    assert!(architecture.contains("not public package boundaries"));
    assert!(architecture.contains("consumer-driven storage slice"));
}

#[test]
fn generic_aggregation_modules_are_absent() {
    let root = package_root().join("src");
    for forbidden in ["common", "shared", "utils", "helpers", "models"] {
        assert!(
            !root.join(forbidden).exists(),
            "generic module {forbidden} must not be added"
        );
    }
}

#[test]
fn owner_and_transport_implementations_remain_external() {
    let root = package_root();
    let manifest = fs::read_to_string(root.join("Cargo.toml")).expect("manifest must be readable");
    for forbidden_dependency in [
        "mote-kernel",
        "mote_kernel",
        "mote-control",
        "mote_control",
        "mote-resource",
        "mote_resource",
        "mote-runtime",
        "mote_runtime",
    ] {
        assert!(
            !manifest.contains(forbidden_dependency),
            "persistence must not depend on {forbidden_dependency}"
        );
    }

    let mut files = Vec::new();
    source_files(&root.join("src"), &mut files);
    for file in files {
        let source = fs::read_to_string(&file).expect("source must be readable");
        for forbidden in [
            "UnixStream",
            "TcpStream",
            "serde_json::Value",
            "dyn Any",
            "Box<dyn Any>",
        ] {
            assert!(
                !source.contains(forbidden),
                "{forbidden} crosses the persistence boundary: {}",
                file.display()
            );
        }
    }
}

#[test]
fn crate_root_does_not_flatten_candidate_modules() {
    let source =
        fs::read_to_string(package_root().join("src/lib.rs")).expect("crate root must be readable");
    assert!(!source.lines().any(|line| {
        let line = line.trim();
        line.starts_with("pub use ") && line.contains("::*")
    }));
}

#[test]
fn source_has_no_nested_cargo_or_git_metadata() {
    let root = package_root();
    let mut stack = vec![root.clone()];
    while let Some(directory) = stack.pop() {
        for entry in fs::read_dir(&directory).expect("package directory must be readable") {
            let path = entry.expect("directory entry must be readable").path();
            let name = path.file_name().and_then(|value| value.to_str());
            if name == Some("target") || name == Some(".agents") || name == Some(".codex") {
                continue;
            }
            if path.is_dir() {
                if name == Some(".git") {
                    assert_eq!(
                        path,
                        root.join(".git"),
                        "nested Git metadata is not allowed: {}",
                        path.display()
                    );
                    continue;
                }
                stack.push(path);
            } else if name == Some("Cargo.toml") {
                assert_eq!(path, root.join("Cargo.toml"));
            }
        }
    }
}
