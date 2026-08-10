# Mote Conformance

This directory owns language-neutral protocol contracts and conformance cases shared by Mote implementations. It contains data, schemas, and normative expectations only; it does not contain a Python, Go, or Rust test runner.

## Layout

```text
conformance/
├── manifest.json
├── schemas/
│   ├── case/          # schemas for test vector and scenario envelopes
│   └── protocol/      # versioned Mote wire schemas
├── vectors/
│   ├── state/         # pure state transition vectors
│   └── wire/          # strict encode/decode vectors
├── scenarios/
│   ├── recovery/      # multi-step crash and resume scenarios
│   └── effects/       # intent, receipt, and reconciliation scenarios
├── traces/            # canonical observable execution traces
└── spec/              # normative prose
```

## Ownership

- This directory owns cross-language schemas, case identity, expected outcomes, and normative semantics.
- `mote-kernel/tests/` owns the Python runner and Python-only tests.
- Future Go and Rust repositories own their runners and implementation-specific tests.
- A case must express externally stable behavior, not implementation details such as classes, exceptions, call stacks, tasks, locks, or storage layout.

## Case discovery

Runners load `manifest.json`, reject unsupported manifest versions, validate every referenced document against its declared schema, and then execute the selected suites. Paths are relative to this directory and use `/` separators.

No suite is enabled until it has a stable protocol schema and at least one reviewed case. Empty suite arrays are valid during bootstrap.

## Compatibility

- Every persisted or wire schema has an explicit version in its schema identifier and payload.
- Unknown versions, unknown tagged variants, missing required fields, unexpected fields, and wrong primitive types fail closed.
- Existing vectors are immutable once released. Corrections add a new case or protocol version and document why the prior case is retired.
- Implementations may add local tests but may not reinterpret a shared expected outcome.

See [spec/conformance.md](spec/conformance.md) for normative runner behavior.
