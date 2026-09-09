#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
failures=0

fail() {
    printf 'structure: %s\n' "$1" >&2
    failures=$((failures + 1))
}

required_files=(
    AGENTS.md
    Cargo.toml
    Cargo.lock
    LICENSE
    Makefile
    NOTICE
    README.md
    README.zh-CN.md
    deny.toml
    rust-toolchain.toml
    src/lib.rs
    src/main.rs
    src/routing.rs
    src/ports/forwarding.rs
    tests/package.rs
    tests/unit.rs
    tests/integration.rs
    tests/architecture.rs
    tests/complexity.rs
    quality/complexity-baseline.json
)

required_directories=(
    docs
    quality
    scripts
    src/api
    src/ports
    src/process
    src/tools
    src/workspace
    third_party_licenses
    tests
)

for relative_path in "${required_files[@]}"; do
    [[ -f "$ROOT/$relative_path" ]] || fail "missing required file: $relative_path"
done

for relative_path in "${required_directories[@]}"; do
    [[ -d "$ROOT/$relative_path" ]] || fail "missing required directory: $relative_path"
done

manifest_count=0
while IFS= read -r -d '' manifest; do
    manifest_count=$((manifest_count + 1))
    [[ "$manifest" == "$ROOT/Cargo.toml" ]] || fail "nested Cargo manifest: ${manifest#$ROOT/}"
done < <(find "$ROOT" -path "$ROOT/target" -prune -o -name Cargo.toml -print0)
[[ "$manifest_count" -eq 1 ]] || fail "expected one Cargo manifest, found $manifest_count"

while IFS= read -r -d '' git_marker; do
    fail "nested Git metadata is not allowed: ${git_marker#$ROOT/}"
done < <(find "$ROOT" -path "$ROOT/target" -prune -o -name .git -print0)

for forbidden in common shared utils helpers models; do
    if find "$ROOT/src" -type d -name "$forbidden" -print -quit | grep -q .; then
        fail "generic source directory is forbidden: $forbidden"
    fi
done

while IFS= read -r -d '' source_file; do
    first_line="$(sed -n '/[^[:space:]]/ {p; q;}' "$source_file")"
    if [[ "$first_line" != '//! '* && "$first_line" != '//!' ]]; then
        fail "production Rust file must start with module documentation: ${source_file#$ROOT/}"
    fi
done < <(find "$ROOT/src" -type f -name '*.rs' -print0)

if [[ "$failures" -ne 0 ]]; then
    printf 'structure: %d failure(s)\n' "$failures" >&2
    exit 1
fi

printf 'structure: ok\n'
