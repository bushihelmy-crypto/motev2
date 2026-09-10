#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

test -f "$root_dir/LICENSE" || { echo 'missing gateway/LICENSE' >&2; exit 1; }
test -f "$root_dir/NOTICE" || { echo 'missing gateway/NOTICE' >&2; exit 1; }
test -f "$root_dir/third_party_licenses/README.md" || {
	echo 'missing gateway/third_party_licenses/README.md' >&2
	exit 1
}

grep -Fq 'Apache License' "$root_dir/LICENSE" || { echo 'LICENSE is not Apache-2.0 text' >&2; exit 1; }
grep -Fq 'END OF TERMS AND CONDITIONS' "$root_dir/LICENSE" || {
	echo 'LICENSE appears truncated' >&2
	exit 1
}
grep -Fq 'Mote Gateway' "$root_dir/NOTICE" || { echo 'NOTICE has no project attribution' >&2; exit 1; }

echo 'gateway license metadata: OK'
