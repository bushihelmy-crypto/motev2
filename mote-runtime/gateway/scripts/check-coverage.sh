#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$root_dir/src"
go_bin="${GO_BIN:-go}"
profile="${COVERAGE_PROFILE:-$root_dir/coverage.out}"

test -f "$profile" || {
	echo "coverage profile not found: $profile" >&2
	exit 1
}

# A multi-package -coverpkg profile repeats instrumented blocks once per test
# binary. A statement is covered when any execution of its unique source block
# has a non-zero count. Check those blocks directly so a rounded 100.0% display
# can never hide a residual uncovered statement.
uncovered_file="$(mktemp)"
trap 'rm -f "$uncovered_file"' EXIT
if ! awk '
	NR == 1 {
		if ($1 != "mode:") {
			exit 2
		}
		next
	}
	NF != 3 {
		exit 2
	}
	{
		key = $1
		statements[key] = $2
		if ($3 > hits[key]) {
			hits[key] = $3
		}
	}
	END {
		if (NR == 1) {
			exit 2
		}
		for (key in statements) {
			if (statements[key] > 0 && hits[key] == 0) {
				printf "%s (%d statements)\n", key, statements[key]
			}
		}
	}
' "$profile" | sort >"$uncovered_file"; then
	echo "invalid coverage profile: $profile" >&2
	exit 1
fi

report="$("$go_bin" -C "$source_dir" tool cover -func="$profile")"
printf '%s\n' "$report"

total="$(printf '%s\n' "$report" | awk '$1 == "total:" { print $3; exit }')"
if [ -s "$uncovered_file" ] || [ "$total" != "100.0%" ]; then
	echo 'coverage gate failed: every instrumented statement must be covered' >&2
	if [ -s "$uncovered_file" ]; then
		echo 'uncovered source blocks:' >&2
		sed 's/^/  /' "$uncovered_file" >&2
	fi
	exit 1
fi

echo 'gateway statement coverage: exact 100%'
