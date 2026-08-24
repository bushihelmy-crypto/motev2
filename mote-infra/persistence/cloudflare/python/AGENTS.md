# Mote Cloudflare Python persistence engineering rules

## Architecture

- This project is the Cloudflare Durable Object SQLite persistence Adapter. It does not own the Worker, Durable Object container, routing, registration, or lookup.
- `mote-kernel` owns the persistence Port and Agent flow semantics. This project satisfies that Port structurally without importing Kernel or reinterpreting Kernel transitions.
- Persistence-backend selection belongs to Port configuration and is independent of Container selection. This Adapter is constructed only when configuration selects it and supplies an object-local Cloudflare storage handle; a Cloudflare Container may instead use a remote backend.
- Raw SQL, schema, migrations, serialization, and multi-statement transaction code belong here and nowhere in `mote-resource/container`.
- Multi-statement SQL changes must use the Durable Object storage transaction primitive. Do not issue raw `BEGIN`, `COMMIT`, `ROLLBACK`, or `SAVEPOINT` statements.
- Do not invent a durable schema, identity encoding, or transition serialization before the owning Kernel contract and matching conformance cases exist.
- This project never mints Agent identities. Control-issued identity and placement are opaque inputs at this boundary.

## Engineering

- Implement the Adapter in Python 3.13 against the Cloudflare Python Workers storage API.
- Keep Cloudflare storage and FFI values inside this Adapter; return caller-owned types through the structural Port.
- Use only packages supported by Cloudflare's Pyodide or PyEmscripten runtime.
- Pin uv and Cloudflare development dependencies; commit `uv.lock`.
- Add deterministic tests for serialization and transaction behavior when the first concrete schema is introduced.
- Preserve user changes and inspect the relevant Git diff before editing.
- Before handoff, run `make check` here and affected monorepo pre-commit hooks, or report precisely which checks could not run.
