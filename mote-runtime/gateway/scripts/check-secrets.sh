#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$(git -C "$root_dir" rev-parse --show-toplevel 2>/dev/null || true)"
detector="${DETECT_SECRETS_HOOK:-detect-secrets-hook}"

command -v "$detector" >/dev/null 2>&1 || {
	echo "${detector} is required for the secret gate (install detect-secrets 1.5.0)" >&2
	exit 1
}
test -n "$repo_root" || {
	echo 'secret scan requires a Git worktree with the monorepo baseline' >&2
	exit 1
}
baseline="$repo_root/.secrets.baseline"
test -f "$baseline" || {
	echo "missing repository secret baseline: ${baseline}" >&2
	exit 1
}

mapfile -d '' -t files < <(find "$root_dir" -type f \
	-not -path "$root_dir/.cache/*" \
	-not -path "$root_dir/.tools/*" \
	-not -name 'coverage.out' -print0 | sort -z)
test "${#files[@]}" -gt 0 || { echo 'no files to scan' >&2; exit 1; }

(
	cd "$repo_root"
	"$detector" --baseline "$baseline" "${files[@]}"
)
echo 'gateway secret scan: OK'
