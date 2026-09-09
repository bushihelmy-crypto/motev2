# Go toolchain policy

The Gateway uses one Go module rooted at `src/`. `src/go.mod` is the language
toolchain source of truth:

- `go 1.26.0` is the minimum supported language version;
- `toolchain go1.26.5` is the reproducible runtime/development baseline;
- `.go-version` repeats the baseline for version managers that understand the
  conventional file (the check script rejects drift).

The quality workflow runs the complete gate on the pinned baseline (`1.26.5`).
Its `gateway-tests` job runs the unit and deterministic integration suites on a
Linux amd64 matrix of the baseline and current stable release (`1.27.1`). The
second version is a compatibility check, not a second API promise; when Go
releases a new stable version, update the matrix and this document together
after reviewing the gate.

Local commands use the toolchain selected by `go.mod`. Go 1.27.1 is already
compatible with the baseline and can run the same commands without changing
the module file. Do not add a second module or a checked-in `go.work` file.

## Required checks

The baseline quality job runs:

```text
make check
```

The command includes formatting, vet, static analysis, unit tests with the race
detector, deterministic integration harness tests, architecture checks,
complexity ratchet, module hygiene, security, license, and a reproducible
build. The compatibility matrix repeats the unit and integration test targets;
live provider tests are never part of CI.
