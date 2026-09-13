#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

expected_toolchain="$(sed -nE 's/^channel = "([0-9]+\.[0-9]+\.[0-9]+)"/\1/p' "$ROOT/rust-toolchain.toml")"
expected_msrv="$(sed -nE 's/^rust-version = "([0-9]+\.[0-9]+)"/\1/p' "$ROOT/Cargo.toml")"

[[ "$expected_toolchain" == "1.85.0" ]] || {
    printf 'toolchain: rust-toolchain.toml must pin 1.85.0 (found %s)\n' "$expected_toolchain" >&2
    exit 1
}
[[ "$expected_msrv" == "1.85" ]] || {
    printf 'toolchain: Cargo.toml must declare MSRV 1.85 (found %s)\n' "$expected_msrv" >&2
    exit 1
}

actual_rust="$(rustc --version | awk '{print $2}')"
actual_cargo="$(cargo --version | awk '{print $2}')"
[[ "$actual_rust" == "$expected_toolchain" ]] || {
    printf 'toolchain: rustc %s is active; expected %s\n' "$actual_rust" "$expected_toolchain" >&2
    exit 1
}
[[ -n "$actual_cargo" ]] || {
    printf 'toolchain: cargo is unavailable\n' >&2
    exit 1
}

command -v rustfmt >/dev/null || { printf 'toolchain: rustfmt is unavailable\n' >&2; exit 1; }
command -v cargo-clippy >/dev/null || { printf 'toolchain: cargo-clippy is unavailable\n' >&2; exit 1; }

printf 'toolchain: rust %s, cargo %s, MSRV %s\n' "$actual_rust" "$actual_cargo" "$expected_msrv"
