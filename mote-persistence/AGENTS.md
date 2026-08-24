# Mote Persistence engineering rules

- This boundary owns concrete persistence, compare-and-swap, schema, migration, serialization, and transaction mechanisms.
- `mote-kernel` owns the `Commit` Port contract and Agent flow semantics. Persistence implementations satisfy the contract structurally and must not import Kernel, Container, Control, Product, or Runtime code.
- Persistence-backend selection belongs to Port configuration and is independent of Container selection. A Container may expose platform capabilities, but it must not select or require a Persistence backend.
- A backend constructor accepts only the capabilities it needs. Cloudflare SQLite accepts Durable Object storage; a remote backend accepts its client or endpoint configuration.
- Public APIs remain narrow. Backend-specific storage handles, SQL cursors, errors, schemas, and transaction types do not cross the Port boundary.
- Cross-language observable contract changes must update `conformance/` and every affected runner in the same change.
- Preserve user changes and inspect the relevant Git diff before editing.
- Read the nearest backend/language `AGENTS.md` before modifying an implementation.
