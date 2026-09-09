#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src_dir="$root_dir/src"

required_dirs=(
	api
	cmd/gateway
	internal/application
	internal/admission
	internal/architecture
	internal/plan
	internal/model
	internal/protocol
	internal/service
	internal/cache
	internal/cache/prompt
	internal/cache/result
	internal/complexity
	internal/usage
	internal/receipt
	internal/telemetry
	internal/testkit
	internal/upstream
	protocols
	connectors
	upstream
	ports
	integration
)

required_files=(
	Makefile
	AGENTS.md
	README.md
	LICENSE
	NOTICE
	.golangci.yaml
	.goreleaser.yaml
	.go-version
	quality/README.md
	quality/complexity-baseline.json
	quality/toolchain.md
	quality/tools.md
	scripts/check-toolchain.sh
	scripts/check-module-hygiene.sh
	scripts/check-license.sh
	scripts/check-secrets.sh
	src/go.mod
	src/doc.go
	src/gateway.go
	src/cmd/gateway/main.go
	src/api/doc.go
	src/internal/cache/doc.go
	src/internal/upstream/doc.go
	src/internal/architecture/package_layout_test.go
	src/internal/complexity/ratchet_test.go
	src/integration/integration_test.go
)

for relative in "${required_dirs[@]}"; do
	test -d "$src_dir/$relative" || {
		echo "missing package directory: src/$relative" >&2
		exit 1
	}
done

for relative in "${required_files[@]}"; do
	test -f "$root_dir/$relative" || {
		echo "missing scaffold file: $relative" >&2
		exit 1
	}
done

mapfile -t go_files < <(find "$src_dir" -type f -name '*.go' -print | sort)
test "${#go_files[@]}" -gt 0 || {
	echo 'no Go files found' >&2
	exit 1
}

for file in "${go_files[@]}"; do
	grep -Eq '^package [a-zA-Z][a-zA-Z0-9_]*$' "$file" || {
		echo "missing package declaration: ${file#"$root_dir/"}" >&2
		exit 1
	}
done

# Every production package has a deliberate documentation owner. Test-only
# packages may contain only doc.go plus *_test.go and are not runtime owners.
while IFS= read -r -d '' directory; do
	has_production_file=0
	while IFS= read -r -d '' file; do
		case "$file" in
			*_test.go) ;;
			*) has_production_file=1; break ;;
		esac
	done < <(find "$directory" -maxdepth 1 -type f -name '*.go' -print0)
	if [ "$has_production_file" -eq 1 ] && [ ! -f "$directory/doc.go" ]; then
		echo "production package has no doc.go: ${directory#"$root_dir/"}" >&2
		exit 1
	fi
done < <(find "$src_dir" -type d -print0)

# These owners are deliberately outside Gateway. Model-native tool calling is
# allowed; MCP discovery/execution, routing, and agent flow are not packages.
for forbidden in mcp router agent common shared util utils helper helpers misc; do
	if find "$src_dir" -type d -iname "$forbidden" -print -quit | grep -q .; then
		echo "forbidden owner directory found under gateway/src: $forbidden" >&2
		exit 1
	fi
done

# The legacy project name is gone; stale names must not leak into source,
# configuration, or project documentation. Exclude this checker because its
# diagnostic pattern necessarily contains the legacy spelling.
if find "$root_dir" -path "$root_dir/.cache" -prune -o -path "$root_dir/.tools" -prune -o \
	-type f ! -path "$root_dir/scripts/check-structure.sh" -print0 |
	xargs -0 -r grep -IlE '(^|[^[:alnum:]_])bty([^[:alnum:]_]|$)' 2>/dev/null | grep -q .; then
	echo 'stale bty identifier found in gateway scaffold' >&2
	exit 1
fi

mapfile -t modules < <(find "$root_dir" -type f -name go.mod \
	-not -path "$root_dir/.cache/*" -not -path "$root_dir/.tools/*" -print)
test "${#modules[@]}" -eq 1 || {
	echo "expected one Go module under gateway, found ${#modules[@]}" >&2
	exit 1
}
test "${modules[0]}" = "$src_dir/go.mod" || {
	echo "Go module must be src/go.mod: ${modules[0]}" >&2
	exit 1
}

if find "$root_dir" -type d -name .git -print -quit | grep -q .; then
	echo 'nested Git repository found under gateway' >&2
	exit 1
fi

echo 'gateway scaffold structure: OK'
