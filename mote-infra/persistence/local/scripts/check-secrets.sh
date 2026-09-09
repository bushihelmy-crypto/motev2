#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DETECTOR="${DETECT_SECRETS_HOOK:-detect-secrets-hook}"

command -v "$DETECTOR" >/dev/null 2>&1 || {
    printf 'secrets: %s is required; install detect-secrets==1.5.0\n' "$DETECTOR" >&2
    exit 1
}

REPO_ROOT="$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$REPO_ROOT" ]]; then
    REPO_ROOT="$(cd "$ROOT/../../.." && pwd)"
fi
BASELINE="$REPO_ROOT/.secrets.baseline"
[[ -f "$BASELINE" ]] || {
    printf 'secrets: repository baseline is missing: %s\n' "$BASELINE" >&2
    exit 1
}

mapfile -d '' -t files < <(
    find "$ROOT" \
        -path "$ROOT/target" -prune -o \
        -path "$ROOT/.git" -prune -o \
        -path "$ROOT/.agents" -prune -o \
        -path "$ROOT/.codex" -prune -o \
        -type f -print0 | sort -z
)
if [[ "${#files[@]}" -eq 0 ]]; then
    printf 'secrets: no package files found\n' >&2
    exit 1
fi

(
    cd "$REPO_ROOT"
    "$DETECTOR" --baseline "$BASELINE" "${files[@]}"
)
printf 'secrets: no un-baselined secrets found\n'
