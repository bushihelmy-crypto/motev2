//! Ownership and package-layout tests.

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
fn expected_owner_directories_are_present() {
    let root = package_root();
    for directory in ["api", "ports", "process", "tools", "workspace"] {
        assert!(
            root.join("src").join(directory).is_dir(),
            "missing src/{directory}"
        );
    }
}

#[test]
fn route_policy_is_a_first_class_boundary() {
    let source = fs::read_to_string(package_root().join("src/routing.rs"))
        .expect("routing boundary must be documented");
    assert!(source.contains("local-versus-remote"));
    assert!(source.contains("immutable execution route configuration"));
    assert!(source.contains("endpoint resolution"));
}

#[test]
fn remote_is_only_a_narrow_capability_boundary() {
    let source = fs::read_to_string(package_root().join("src/ports/forwarding.rs"))
        .expect("remote capability port must be documented");
    assert!(source.contains("Narrow capability"));
    assert!(source.contains("mote-infra/invocation"));
    assert!(source.contains("does not define a wire DTO"));
}

#[test]
fn generic_aggregation_modules_and_transport_implementations_are_absent() {
    let root = package_root().join("src");
    for forbidden in ["common", "shared", "utils", "helpers", "models"] {
        assert!(
            !root.join(forbidden).exists(),
            "generic module {forbidden} must not be added"
        );
    }

    let mut files = Vec::new();
    source_files(&root, &mut files);
    for file in files {
        let source = fs::read_to_string(&file).expect("source must be readable");
        assert!(
            !source.contains("UnixStream"),
            "transport belongs to invocation infra: {}",
            file.display()
        );
        assert!(
            !source.contains("TcpStream"),
            "transport belongs to invocation infra: {}",
            file.display()
        );
        assert!(
            !source.contains("serde_json"),
            "wire DTOs belong to conformance boundary: {}",
            file.display()
        );
    }
}

#[test]
fn source_has_no_nested_cargo_or_git_metadata() {
    let root = package_root();
    let mut stack = vec![root.clone()];
    while let Some(directory) = stack.pop() {
        for entry in fs::read_dir(&directory).expect("package directory must be readable") {
            let path = entry.expect("directory entry must be readable").path();
            if path.file_name().is_some_and(|name| name == "target") {
                continue;
            }
            if path.is_dir() {
                assert_ne!(
                    path.file_name().and_then(|name| name.to_str()),
                    Some(".git")
                );
                stack.push(path);
            } else if path.file_name().is_some_and(|name| name == "Cargo.toml") {
                assert_eq!(path, root.join("Cargo.toml"));
            }
        }
    }
}
