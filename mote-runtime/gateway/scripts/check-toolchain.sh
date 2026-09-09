#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
module_file="$root_dir/src/go.mod"
version_file="$root_dir/.go-version"
go_bin="${GO_BIN:-go}"

test -f "$module_file" || {
	echo "missing module file: ${module_file#"$root_dir/"}" >&2
	exit 1
}
test -f "$version_file" || {
	echo "missing pinned toolchain file: .go-version" >&2
	exit 1
}

required_version="$(awk '$1 == "go" { print $2; exit }' "$module_file")"
pinned_version="$(tr -d '[:space:]' < "$version_file")"
toolchain_version="$(awk '$1 == "toolchain" { sub(/^go/, "", $2); print $2; exit }' "$module_file")"

test -n "$required_version" || {
	echo 'go.mod has no go directive' >&2
	exit 1
}
test -n "$toolchain_version" || {
	echo 'go.mod has no toolchain directive' >&2
	exit 1
}
test "$toolchain_version" = "$pinned_version" || {
	echo "go.mod toolchain (${toolchain_version}) must match .go-version (${pinned_version})" >&2
	exit 1
}

lowest_pinned="$(printf '%s\n' "$pinned_version" "$required_version" | sort -V | head -n 1)"
if [ "$lowest_pinned" != "$required_version" ]; then
	echo "pinned Go ${pinned_version} is older than the required ${required_version}" >&2
	exit 1
fi

command -v "$go_bin" >/dev/null 2>&1 || {
	echo "Go toolchain not found: ${go_bin}" >&2
	exit 1
}

actual_version="$($go_bin version | awk '{ print $3 }' | sed 's/^go//')"
case "$actual_version" in
	[0-9]*.[0-9]*.[0-9]*) ;;
	*)
		echo "unable to parse Go version from '$actual_version'" >&2
		exit 1
		;;
esac

lowest_pinned="$(printf '%s\n' "$actual_version" "$pinned_version" | sort -V | head -n 1)"
if [ "$lowest_pinned" != "$pinned_version" ]; then
	echo "Go ${actual_version} is older than the pinned runtime baseline ${pinned_version}" >&2
	exit 1
fi

printf 'gateway toolchain: Go %s (language minimum %s, runtime baseline %s)\n' "$actual_version" "$required_version" "$pinned_version"
