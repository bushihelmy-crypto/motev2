#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for required in LICENSE NOTICE third_party_licenses/README.md; do
    [[ -f "$ROOT/$required" ]] || {
        printf 'license: missing %s\n' "$required" >&2
        exit 1
    }
done

grep -q 'Apache License' "$ROOT/LICENSE" || {
    printf 'license: LICENSE is not Apache-2.0 text\n' >&2
    exit 1
}
grep -q 'Version 2.0' "$ROOT/LICENSE" || {
    printf 'license: Apache version marker is missing\n' >&2
    exit 1
}
grep -q 'END OF TERMS AND CONDITIONS' "$ROOT/LICENSE" || {
    printf 'license: LICENSE appears truncated\n' >&2
    exit 1
}
grep -Eq '^license = "Apache-2.0"$' "$ROOT/Cargo.toml" || {
    printf 'license: Cargo.toml license metadata must be Apache-2.0\n' >&2
    exit 1
}
grep -Eq 'Mote (Local Infrastructure )?Persistence' "$ROOT/NOTICE" || {
    printf 'license: NOTICE must identify this package\n' >&2
    exit 1
}
grep -qi 'third-party' "$ROOT/NOTICE" || {
    printf 'license: NOTICE must reserve third-party attribution space\n' >&2
    exit 1
}

printf 'license: metadata and attribution files are valid\n'
