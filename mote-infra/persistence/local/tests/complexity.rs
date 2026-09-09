//! Deterministic production-code complexity ratchet.
//!
//! This uses a small dependency-free scanner while the package has no runtime
//! dependencies. The baseline is exact: both growth and an unrecorded
//! reduction fail, so the metric cannot drift silently.

use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

const METRIC_NAMES: [&str; 7] = [
    "production_files",
    "code_lines",
    "function_like_items",
    "decision_points",
    "public_items",
    "unsafe_occurrences",
    "max_file_code_lines",
];

fn package_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn rust_files(root: &Path, output: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(root).expect("source directory must be readable") {
        let path = entry.expect("directory entry must be readable").path();
        if path.is_dir() {
            rust_files(&path, output);
        } else if path.extension().is_some_and(|extension| extension == "rs") {
            output.push(path);
        }
    }
}

fn occurrences(text: &str, needle: &str) -> u64 {
    text.match_indices(needle).count() as u64
}

fn is_code_line(line: &str) -> bool {
    let trimmed = line.trim();
    !trimmed.is_empty()
        && !trimmed.starts_with("//")
        && !trimmed.starts_with('*')
        && !trimmed.starts_with("/*")
        && !trimmed.starts_with("*/")
}

fn snapshot() -> BTreeMap<String, u64> {
    let mut files = Vec::new();
    rust_files(&package_root().join("src"), &mut files);
    files.sort();

    let mut metrics = BTreeMap::new();
    let mut code_lines = 0_u64;
    let mut function_like_items = 0_u64;
    let mut decision_points = 0_u64;
    let mut public_items = 0_u64;
    let mut unsafe_occurrences = 0_u64;
    let mut max_file_code_lines = 0_u64;

    for file in &files {
        let source = fs::read_to_string(file).expect("production source must be readable");
        let mut file_code_lines = 0_u64;
        for line in source.lines().filter(|line| is_code_line(line)) {
            file_code_lines += 1;
            code_lines += 1;
            let trimmed = line.trim();
            if trimmed.contains("fn ") || trimmed.contains("fn(") {
                function_like_items += 1;
            }
            if trimmed.starts_with("pub ") || trimmed.starts_with("pub(") {
                public_items += 1;
            }
            decision_points += occurrences(trimmed, "if ")
                + occurrences(trimmed, "match ")
                + occurrences(trimmed, "for ")
                + occurrences(trimmed, "while ")
                + occurrences(trimmed, "loop {")
                + occurrences(trimmed, "&&")
                + occurrences(trimmed, "||");
            unsafe_occurrences += occurrences(trimmed, "unsafe");
        }
        max_file_code_lines = max_file_code_lines.max(file_code_lines);
    }

    metrics.insert("production_files".to_owned(), files.len() as u64);
    metrics.insert("code_lines".to_owned(), code_lines);
    metrics.insert("function_like_items".to_owned(), function_like_items);
    metrics.insert("decision_points".to_owned(), decision_points);
    metrics.insert("public_items".to_owned(), public_items);
    metrics.insert("unsafe_occurrences".to_owned(), unsafe_occurrences);
    metrics.insert("max_file_code_lines".to_owned(), max_file_code_lines);
    metrics
}

fn baseline() -> BTreeMap<String, u64> {
    let text = fs::read_to_string(package_root().join("quality/complexity-baseline.json"))
        .expect("complexity baseline must be readable");
    let mut values = BTreeMap::new();
    for line in text.lines() {
        let Some((key, value)) = line.trim().trim_end_matches(',').split_once(':') else {
            continue;
        };
        let key = key.trim().trim_matches('"');
        if !METRIC_NAMES.contains(&key) {
            continue;
        }
        let value = value
            .trim()
            .trim_matches('"')
            .parse::<u64>()
            .expect("baseline values must be integers");
        assert!(
            values.insert(key.to_owned(), value).is_none(),
            "baseline contains duplicate metric {key}"
        );
    }
    values
}

fn report(actual: &BTreeMap<String, u64>, expected: &BTreeMap<String, u64>) -> String {
    METRIC_NAMES
        .iter()
        .map(|name| format!("{name}: {} -> {}", expected[*name], actual[*name]))
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn baseline_names_are_complete_and_unique() {
    let expected = baseline();
    assert_eq!(
        expected.len(),
        METRIC_NAMES.len(),
        "baseline must contain every metric exactly once"
    );
    for name in METRIC_NAMES {
        assert!(expected.contains_key(name), "baseline is missing {name}");
    }
}

#[test]
fn production_complexity_matches_exact_baseline() {
    let actual = snapshot();
    let expected = baseline();
    let regressions: Vec<_> = METRIC_NAMES
        .iter()
        .filter_map(|name| (actual[*name] > expected[*name]).then_some(*name))
        .collect();
    let reductions: Vec<_> = METRIC_NAMES
        .iter()
        .filter_map(|name| (actual[*name] < expected[*name]).then_some(*name))
        .collect();
    assert!(
        regressions.is_empty(),
        "complexity grew ({regressions:?})\n{}",
        report(&actual, &expected)
    );
    assert!(
        reductions.is_empty(),
        "complexity baseline is stale ({reductions:?})\n{}",
        report(&actual, &expected)
    );
}

#[test]
fn complexity_report_is_available_for_ci_logs() {
    if env::var_os("MOTE_COMPLEXITY_REPORT").is_some() {
        let actual = snapshot();
        let expected = baseline();
        println!(
            "production complexity ratchet:\n{}",
            report(&actual, &expected)
        );
    }
}
