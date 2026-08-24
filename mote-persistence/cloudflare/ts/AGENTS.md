# Mote Cloudflare TypeScript persistence engineering rules

## Architecture

- This project is the TypeScript Cloudflare Durable Object SQLite persistence Adapter. It does not own the Worker, Durable Object container, routing, registration, or lookup.
- Upper layers own Agent flow semantics. This lowest-level package must not import Kernel, Container, Control, or Product code.
- Persistence-backend selection belongs to Port configuration and is independent of Container selection. This Adapter is constructed only when configuration selects it and supplies an object-local Cloudflare storage handle; a Cloudflare Container may instead use a remote backend.
- Raw SQL, schema, migrations, serialization, and multi-statement transaction code belong here and nowhere in `mote-container`.
- The package exports exactly one symbol: `Commit`. All storage types, errors, and helpers remain private.
- Multi-statement SQL changes must use `transactionSync()`. Do not issue raw `BEGIN`, `COMMIT`, `ROLLBACK`, or `SAVEPOINT` statements.
- Do not invent a durable schema, identity encoding, or transition serialization before the owning contract and matching conformance cases exist.
- This project never mints Agent identities. Control-issued identity and placement are opaque inputs at this boundary.

## Engineering

- Keep ESM, strict TypeScript, and explicit `.ts` extensions for local source imports.
- Keep Cloudflare storage values inside this Adapter and return contract-owned values through the Port.
- Pin pnpm and all dependencies; commit `pnpm-lock.yaml`.
- Test persistence and transaction behavior against workerd's real Durable Object storage; never substitute a host-local SQLite database.
- This package is a Worker dependency and is not deployed or published independently.
- Preserve user changes and inspect the relevant Git diff before editing.
- Before handoff, run `pnpm run check` here and affected monorepo pre-commit hooks, or report precisely which checks could not run.
