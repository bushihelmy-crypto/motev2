# Mote Cloudflare Container engineering rules

## Architecture

- Implement this project in pure TypeScript against Cloudflare Workers and Durable Objects APIs.
- One logical Agent maps to one Durable Object identity. Do not store cross-Agent mutable state in a single object.
- `mote-control` owns Agent identity, lineage, placement, and registration. This Container must consume Control-issued identities rather than minting them.
- `mote-kernel` owns Agent flow semantics. This Container must not interpret Agent flow, Think, Act, Context, Spawn intent, or Product presentation semantics.
- Do not invent a request protocol, durable schema, identity encoding, or public routing surface before a Kernel consumer and corresponding conformance contract exist.
- State that must survive eviction, restart, or deployment goes through the backend independently selected by Port configuration. Instance fields are caches only and must be reconstructible from durable state.
- Cloudflare Container selection does not imply Cloudflare SQLite persistence. The Container may expose `ctx.storage` as a platform capability, while Port configuration may instead select a remote persistence backend.
- New Durable Object classes declare the SQLite storage binding required by the Cloudflare deployment in `wrangler.jsonc`.
- If selected, raw Cloudflare SQL, schema, and transaction code belong to `mote-infra/persistence/cloudflare/ts`, not this Container project. Do not call `ctx.storage.sql` or `transactionSync()` here.

## Engineering

- Keep ESM, strict TypeScript, and explicit `.ts` extensions for local source imports.
- Pin the package manager and commit `pnpm-lock.yaml`. CI installs with `--frozen-lockfile`.
- Generate the small binding declaration with `pnpm run types` after changing `wrangler.jsonc` or the compatibility date. Runtime declarations come from the pinned `@cloudflare/workers-types` dependency and must not be generated into the source tree.
- Add workerd-backed tests for Durable Object identity, eviction, alarm, and concurrency behavior as those mechanisms are introduced.
- Keep the Worker entry point narrow. Product-owned HTTP routing and Kernel-owned Agent behavior do not belong here.
- New dependencies require a concrete consumer. This Worker is a deployment artifact and is not published to npm.
- Preserve user changes and inspect the relevant Git diff before editing.
- Before handoff, run `pnpm run check` and the affected monorepo pre-commit hooks, or report precisely which checks could not run.
