# Mote Infra engineering rules

## Architecture

- This project starts as one Rust package. The durable, protocol, and infrad directories are a provisional exploration, not settled package boundaries.
- Do not freeze the provisional layout with dependency guards, public imports, or compatibility promises. Confirm boundaries from the first complete consumer-driven vertical slice.
- If retained, durable is the candidate home for language-neutral reliability mechanisms implemented in Rust. Infra must not interpret Agent flow, Think, Act, Context, Spawn intent, or other owner-specific business semantics.
- If retained, protocol implements the Rust side of contracts owned by the monorepo root conformance directory. Infra must not introduce independent wire DTOs, versions, identity rules, or error semantics.
- If retained, infrad is a candidate composition boundary for configuration, listeners, telemetry, and graceful shutdown rather than state and transaction rules.
- SQLite, local CAS, and future storage engines are private adapters behind narrow typed ports. Raw SQL connections, storage-engine transaction types, and generated wire types must not cross owner-facing boundaries.
- Do not create generic common, shared, utils, helpers, or models modules. Put a type with the invariant it represents.
- Do not add placeholder protocol fields or infer a public composition API before the first consumer and corresponding conformance contract exist.

## Engineering references

- Turso may be used as a non-normative reference for Rust project organization, formatting, Clippy, focused test commands, deterministic simulation, and failure-injection discipline.
- Turso does not define Mote interfaces or ownership boundaries.
- Do not copy Turso implementation code into this project. If source adaptation is explicitly approved later, preserve its license and attribution and record the adaptation under third_party_licenses.

## Engineering

- Use Rust 2024 and keep the minimum supported Rust version declared in Cargo.toml.
- Prefer immutable values, typed enums, explicit state transitions, and narrow ports.
- Keep imports at module scope unless conditional compilation or a macro expansion requires a narrower scope.
- Keep the crate root narrow. Owner modules explicitly re-export confirmed public types; do not use glob re-exports to flatten internal modules.
- Preserve generic relationships from request through port, adapter, and result. Do not erase a durable boundary into dyn Any, Box<dyn Any>, an untyped map, or an untyped JSON value.
- When a generic contract is introduced, test it with more than one concrete payload type so adapters cannot accidentally specialize it.
- If generated wire code is introduced, isolate it at the transport boundary; hand-written durable mechanisms must not depend directly on generated types.
- Add deterministic tests for every CAS, atomic commit, receipt replay, fencing, crash-recovery, and workspace-conflict boundary.
- Add architecture and generic-preservation guards only after the guarded production contract exists; do not test a speculative abstraction.
- New dependencies need a concrete consumer and must not define Mote architecture by convenience.
- Preserve user changes and inspect the relevant Git diff before editing.
- Before handoff, run make check and the monorepo pre-commit checks, or report precisely which checks could not run.
