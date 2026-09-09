#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$ROOT/Cargo.toml"

[[ -f "$ROOT/Cargo.lock" ]] || { printf 'cargo hygiene: Cargo.lock is required\n' >&2; exit 1; }
[[ -f "$ROOT/.gitignore" ]] || { printf 'cargo hygiene: .gitignore is required\n' >&2; exit 1; }
grep -q '^/target/$' "$ROOT/.gitignore" || {
    printf 'cargo hygiene: target/ must be ignored at package scope\n' >&2
    exit 1
}
if grep -Eq '^\[workspace([.]|\])' "$MANIFEST"; then
    printf 'cargo hygiene: package must remain a standalone manifest\n' >&2
    exit 1
fi

metadata="$(cargo metadata --manifest-path "$MANIFEST" --locked --no-deps --format-version 1)"
grep -q '"name":"mote-runtime-execution-local"' <<<"$metadata" || {
    printf 'cargo hygiene: package metadata name mismatch\n' >&2
    exit 1
}

if find "$ROOT" -path "$ROOT/target" -prune -o -type f \( -name '*.crate' -o -name '*.rlib' -o -name '*.rmeta' \) -print -quit | grep -q .; then
    printf 'cargo hygiene: build artifacts must stay under target/\n' >&2
    exit 1
fi

printf 'cargo hygiene: metadata, lockfile, and artifact boundaries are valid\n'
