# Mote Cloudflare deployment engineering rules

## Architecture

- Implement this single, flat project in pure TypeScript against Cloudflare Workers and Durable Objects APIs. Do not recreate Python or nested language variants.
- One logical Agent maps to one Durable Object identity. Do not store cross-Agent mutable state in a single object.
- `mote-control` owns Agent identity, lineage, placement, and registration. This Container must consume Control-issued identities rather than minting them.
- `mote-kernel` owns Agent flow semantics. This Container must not interpret Agent flow, Think, Act, Context, Spawn intent, or Product presentation semantics.
- Do not invent a request protocol, durable schema, identity encoding, or public routing surface before a Kernel consumer and corresponding conformance contract exist.
- State that must survive eviction, restart, or deployment goes through the backend independently selected by Port configuration. Instance fields are caches only and must be reconstructible from durable state.
- Cloudflare Container selection does not imply Cloudflare SQLite persistence. Port configuration may select the co-located object-local Adapter or a remote persistence backend.
- New Durable Object classes declare the SQLite storage binding required by the Cloudflare deployment in `wrangler.jsonc`.
- This project is the only Cloudflare deployment composition root. The Durable Object receives `ctx`; when object-local persistence is selected, it supplies `ctx.storage` to the Adapter.
- Keep raw Cloudflare SQL, schema, migrations, serialization, and transaction code isolated in `src/persistence.ts`. Do not duplicate those mechanisms in the Worker/Container entry point.
- `ctx`, `ctx.storage`, SQL cursors, Durable Object IDs, and Container handles never cross the Kernel Port or Invocation contract.

## Engineering

- Keep ESM, strict TypeScript, and explicit `.ts` extensions for local source imports.
- Pin the package manager and commit `pnpm-lock.yaml`. CI installs with `--frozen-lockfile`.
- Generate the small binding declaration with `pnpm run types` after changing `wrangler.jsonc` or the compatibility date. Runtime declarations come from the pinned `@cloudflare/workers-types` dependency and must not be generated into the source tree.
- Add workerd-backed tests for Durable Object identity, persistence transactions, eviction, alarm, and concurrency behavior as those mechanisms are introduced.
- Keep the Worker entry point narrow. Product-owned HTTP routing and Kernel-owned Agent behavior do not belong here.
- New dependencies require a concrete consumer. This Worker is a deployment artifact and is not published to npm.
- Preserve user changes and inspect the relevant Git diff before editing.
- Before handoff, run `pnpm run check` and the affected monorepo pre-commit hooks, or report precisely which checks could not run.
