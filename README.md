# Mote v2

Mote v2 is a multi-language agent system organized as a monorepo. Python defines agent flow semantics, while language-neutral conformance contracts keep future Go and Rust implementations aligned.

## Repository layout

```text
motev2/
├── conformance/     # Language-neutral schemas, vectors, scenarios, and traces
├── mote-kernel/     # Python single-Agent kernel
├── mote-go/         # Future Go control plane
└── mote-rust/       # Future Rust execution and state services
```

Each implementation project owns its build system and implementation-specific tests. Cross-language protocol changes and their conformance cases are committed atomically at this repository root.
