#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$root_dir/src"
go_bin="${GO_BIN:-go}"
expected_module='github.com/bushihelmy-crypto/motev2/mote-runtime/gateway'
export GOCACHE="${GOCACHE:-$root_dir/.cache/go-build}"
export GOMODCACHE="${GOMODCACHE:-$root_dir/.cache/go-mod}"
export GOTOOLCHAIN="${GOTOOLCHAIN:-local}"

mapfile -t modules < <(find "$root_dir" -type f -name go.mod \
	-not -path "$root_dir/.cache/*" -not -path "$root_dir/.tools/*" -print | sort)
test "${#modules[@]}" -eq 1 || {
	echo "expected exactly one gateway go.mod, found ${#modules[@]}: ${modules[*]}" >&2
	exit 1
}
test "${modules[0]}" = "$source_dir/go.mod" || {
	echo "the gateway module must live at src/go.mod: ${modules[0]}" >&2
	exit 1
}

for forbidden in "$root_dir/go.work" "$root_dir/go.work.sum" "$source_dir/vendor"; do
	if [ -e "$forbidden" ]; then
		echo "forbidden module artifact found: ${forbidden#"$root_dir/"}" >&2
		exit 1
	fi
done

module_name="$(awk '$1 == "module" { print $2; exit }' "$source_dir/go.mod")"
test "$module_name" = "$expected_module" || {
	echo "unexpected module path: ${module_name:-<missing>}" >&2
	exit 1
}

"$go_bin" -C "$source_dir" mod tidy -diff
"$go_bin" -C "$source_dir" mod verify
"$go_bin" -C "$source_dir" list -mod=readonly ./... >/dev/null

echo 'gateway module hygiene: OK'
