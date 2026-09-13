#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/../../.." && pwd)"
BASELINE="$REPO_ROOT/.secrets.baseline"

command -v detect-secrets-hook >/dev/null || {
    printf 'secrets: detect-secrets-hook is required; install detect-secrets==1.5.0\n' >&2
    exit 1
}
[[ -f "$BASELINE" ]] || {
    printf 'secrets: repository baseline is missing: %s\n' "$BASELINE" >&2
    exit 1
}

mapfile -d '' files < <(
    find "$ROOT" \
        -path "$ROOT/target" -prune -o \
        -path "$ROOT/.git" -prune -o \
        -type f -print0
)
if [[ "${#files[@]}" -eq 0 ]]; then
    printf 'secrets: no package files found\n' >&2
    exit 1
fi

detect-secrets-hook --baseline "$BASELINE" "${files[@]}"
printf 'secrets: no un-baselined secrets found\n'
