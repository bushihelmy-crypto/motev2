# Mote Cloudflare Infra engineering rules

## Architecture

- Implement this project in pure TypeScript against Cloudflare Workers and Durable Objects APIs.
- One logical Agent maps to one Durable Object identity. Do not store cross-Agent mutable state in a single object.
- Infra implements execution and persistence mechanisms; it must not interpret Agent flow, Think, Act, Context, Spawn intent, or Product presentation semantics.
- Do not invent a request protocol, durable schema, identity encoding, or public routing surface before a Kernel consumer and corresponding conformance contract exist.
- State that must survive eviction, restart, or deployment belongs in Durable Object storage. Instance fields are caches only and must be reconstructible from durable state.
- New Durable Object classes use the SQLite storage backend declared in `wrangler.jsonc`.
- Use `ctx.storage.transactionSync()` for multi-statement SQL transactions. Do not issue `BEGIN`, `COMMIT`, `ROLLBACK`, or `SAVEPOINT` through `sql.exec()`.
- Raw `SqlStorage`, Durable Object state, and Cloudflare binding types stay inside this Infra adapter.

## Engineering

- Keep ESM, strict TypeScript, and explicit `.ts` extensions for local source imports.
- Pin the package manager and commit `pnpm-lock.yaml`. CI installs with `--frozen-lockfile`.
- Generate the small binding declaration with `pnpm run types` after changing `wrangler.jsonc` or the compatibility date. Runtime declarations come from the pinned `@cloudflare/workers-types` dependency and must not be generated into the source tree.
- Add workerd-backed tests for persistence, transaction, eviction, alarm, and concurrency behavior as those mechanisms are introduced.
- Keep the Worker entry point narrow. Product-owned HTTP routing and Kernel-owned Agent behavior do not belong here.
- New dependencies require a concrete consumer. This Worker is a deployment artifact and is not published to npm.
- Preserve user changes and inspect the relevant Git diff before editing.
- Before handoff, run `pnpm run check` and the affected monorepo pre-commit hooks, or report precisely which checks could not run.
