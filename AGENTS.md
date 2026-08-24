# Mote v2 monorepo rules

- The repository root owns cross-language architecture, conformance contracts, and coordinated CI.
- Each language project owns its implementation, dependencies, build configuration, local tests, and release artifact.
- Cross-language DTO and durable protocol changes MUST update `conformance/` and affected implementation runners in one change.
- Do not create nested Git repositories.
- Read the nearest child `AGENTS.md` before modifying a project. More specific child rules supplement these root rules.
- Preserve strict owner boundaries: Python Kernel owns Agent flow semantics and persistence Port contracts, Go Control owns control-plane mechanisms, `mote-resource` owns resource registration/discovery, `mote-resource/container` owns Container registration/lookup/uniform invocation and hosting capabilities, `mote-resource/embodiment` owns physical-body resource handles, `mote-infra/persistence` owns persistence and transaction mechanisms, `mote-infra/rpc` owns reusable transport mechanics, and `conformance/` owns shared observable contracts.
- Container choice and persistence-backend choice are orthogonal. Port configuration selects the persistence implementation; a Container may expose platform resources such as Durable Object storage but must not select or require that backend.
- Do not copy implementation code between languages to simulate reuse. Reuse stable schemas and behavioral vectors.
