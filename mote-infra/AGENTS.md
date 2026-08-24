# Mote Infrastructure engineering rules

- This boundary owns concrete infrastructure adapters, including persistence and RPC transport implementations.
- `mote-kernel` owns Agent flow semantics and Port contracts; infrastructure projects implement those contracts without importing Kernel flow code.
- `mote-resource/container` is a separate hosting boundary under the
  `mote-resource` umbrella. It may expose platform capabilities and inject a
  configured Port resolver before starting Kernel, but it must not own
  persistence or RPC semantics.
- Persistence selection and Container selection remain independent. A Container may provide a storage capability without selecting the persistence adapter that consumes it.
- RPC transport implementations belong under `rpc/`; business protocols and cross-language observable schemas remain owned by their respective boundary and `conformance/`.
- Do not invent a generic shared module or a public protocol before a concrete consumer and matching contract exist.
- Each language/backend project owns its dependencies, lockfiles, tests, and release artifact.
- Preserve user changes and inspect the relevant Git diff before editing.
- Read the nearest child `AGENTS.md` before changing a concrete implementation.
