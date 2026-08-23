# Mote v2 monorepo rules

- The repository root owns cross-language architecture, conformance contracts, and coordinated CI.
- Each language project owns its implementation, dependencies, build configuration, local tests, and release artifact.
- Cross-language DTO and durable protocol changes MUST update `conformance/` and affected implementation runners in one change.
- Do not create nested Git repositories.
- Read the nearest child `AGENTS.md` before modifying a project. More specific child rules supplement these root rules.
- Preserve strict owner boundaries: Python owns Agent flow semantics, Go owns control-plane mechanisms, deployment-specific Infra implementations own execution and state mechanisms, and `conformance/` owns shared observable contracts.
- Do not copy implementation code between languages to simulate reuse. Reuse stable schemas and behavioral vectors.
