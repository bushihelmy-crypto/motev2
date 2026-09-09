# Gateway development contract

This directory is a Go project scaffold, not a second protocol specification.
The authoritative implementation contracts will live in `src/api` only after
their owner, lifecycle, failure semantics, and first consumer are accepted.
Cross-language or durable shapes must additionally be represented in the root
`conformance/` directory in the same change.

## Change checklist

1. Identify the single owner for the new concept.
2. Add or update one canonical contract; do not duplicate provider DTOs in a
   shared package.
3. Keep model, protocol, service, and transport dependencies one-way.
4. Add deterministic unit tests at the owning boundary.
5. Update conformance schemas/vectors when the shape is observable outside Go.
6. Run `make check` and record any unavailable external integration checks.

Provider integration tests must use local deterministic fixtures by default.
Live API tests are opt-in and must never be required for the package quality
gate.
