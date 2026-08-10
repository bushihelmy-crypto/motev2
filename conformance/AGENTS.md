# Conformance repository rules

- This directory is language neutral. Do not add Python, Go, Rust, shell, or CI-specific runner implementations here.
- JSON Schema and JSON cases are authoritative cross-language contracts. Keep them strict, versioned, deterministic, and free of implementation details.
- Do not add protocol fields before their owner, semantics, lifecycle, failure behavior, and first consumer are confirmed.
- Released cases are immutable. Correct semantics through a new case or protocol version with documented retirement of the superseded case.
- Unknown versions, tags, missing fields, extra fields, and wrong primitive types fail closed.
- Never encode secrets, environment-specific paths, timestamps from the current clock, random identities, or unstable error text in cases.
