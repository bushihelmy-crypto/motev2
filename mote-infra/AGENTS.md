# Mote Infrastructure engineering rules

- This boundary has two parallel infrastructure owners: `invocation/` is the sole owner of invocation infrastructure, and `persistence/` is the sole owner of storage infrastructure.
- `mote-kernel` owns Agent flow semantics and Port contracts; infrastructure projects implement those contracts without importing Kernel flow code.
- `mote-resource/container` is a separate hosting boundary under the
  `mote-resource` umbrella. It may expose platform capabilities and inject a
  configured Port resolver before starting Kernel, but it must not own
  persistence or invocation semantics.
- Persistence selection and Container selection remain independent. A Container may provide a storage capability without selecting the persistence adapter that consumes it.
- Invocation contracts, explicit resolution, local implementations, and RPC implementations belong under `invocation/`. RPC is one invocation implementation, not a parallel owner.
- Persistence, compare-and-swap, schemas, migrations, and transaction mechanisms belong under `persistence/`; invocation must not become a state store.
- Business protocols and cross-language observable schemas remain owned by their respective boundary and `conformance/`.
- Do not invent a generic shared module or a public protocol before a concrete consumer and matching contract exist.
- Each language/backend project owns its dependencies, lockfiles, tests, and release artifact.
- Preserve user changes and inspect the relevant Git diff before editing.
- Read the nearest child `AGENTS.md` before changing a concrete implementation.
