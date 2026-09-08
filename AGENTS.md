# Mote v2 monorepo rules

- The repository root owns cross-language architecture, conformance contracts, and coordinated CI.
- Each language project owns its implementation, dependencies, build configuration, local tests, and release artifact.
- Cross-language DTO and durable protocol changes MUST update `conformance/` and affected implementation runners in one change.
- Do not create nested Git repositories.
- Read the nearest child `AGENTS.md` before modifying a project. More specific child rules supplement these root rules.
- Preserve strict owner boundaries: Python Kernel owns Agent flow semantics and persistence Port contracts, Go Control owns control-plane mechanisms, `mote-resource` owns resource registration/discovery, `mote-resource/container` owns Container registration/lookup and hosting capabilities, `mote-resource/embodiment` owns physical-body resource handles, `mote-infra/invocation` is the sole owner of invocation contracts, resolution, and local/remote invocation mechanics, `mote-infra/persistence` owns portable persistence and transaction implementations, and `conformance/` owns shared observable contracts. The one platform-bound exception is Cloudflare Durable Object SQLite: its Adapter is co-located in `mote-resource/container/cloudflare` because only that deployed Durable Object receives `ctx.storage`; keep its SQL isolated in `src/persistence.ts` behind the same Kernel-owned Port contract.
- Container choice and persistence-backend choice are orthogonal. Port configuration selects the persistence implementation; a Container may expose platform resources such as Durable Object storage but must not select or require that backend.
- Do not copy implementation code between languages to simulate reuse. Reuse stable schemas and behavioral vectors.
