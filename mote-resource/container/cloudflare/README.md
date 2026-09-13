# Mote Container for Cloudflare

This is Mote's single TypeScript Cloudflare deployment project. It contains the Worker/Durable Object Container host and the object-local Durable Object SQLite persistence Adapter in one bundle, while keeping Agent identity in `mote-control`, Agent flow semantics in `mote-kernel`, and backend selection in Port configuration.

The project is in its bootstrap phase. The Durable Object class, binding, and existing CAS `Commit` Adapter are deployable together, but no Agent request endpoint or Product route has been fixed yet. Those interfaces will be introduced by the first consumer-driven vertical slice and its conformance cases.

## Runtime model

- `AgentDurableObject` is the Cloudflare container for one logical Agent.
- A Control-issued stable Agent identity will select one Durable Object identity when the shared identity contract is defined.
- The Container will call Kernel contracts without interpreting Agent flow semantics.
- Durable state passes through the backend selected by Port configuration. The choice may be the co-located Cloudflare SQLite Adapter or a remote store.
- The default Worker currently returns `404`, and the Durable Object returns `501`, so the scaffold does not accidentally establish a Product API.

The Durable Object namespace is declared with Cloudflare's current declarative `exports` configuration and `storage: "sqlite"`. This exposes the optional storage capability but does not select it. If Port configuration selects object-local storage, this same Durable Object supplies its runtime-injected `ctx.storage` to `src/persistence.ts`; SQL, schema, serialization, and transactions stay isolated in that module. The older `migrations[].new_sqlite_classes` form is intentionally not used for this new Worker.

Container and persistence choices remain orthogonal even though the Cloudflare implementation is physically co-located. Selecting a remote backend bypasses object-local SQLite. Neither `ctx.storage` nor any Cloudflare storage type crosses the Kernel Port or Invocation contract.

## Development

Node 24 is the primary development and quality-gate version. CI also runs the test suite on Node 22.19 and Node 26. The package manager version is pinned in `package.json`.

```bash
pnpm install --frozen-lockfile
pnpm run types
pnpm run check
```

Run a local Worker with:

```bash
pnpm run dev
```

Build the deployment bundle without publishing it:

```bash
pnpm run build
```

Deploy only after authenticating Wrangler and selecting the intended Cloudflare account:

```bash
pnpm run deploy
```

## Package status

This flat package is private because its release artifact is one deployed Cloudflare Worker, not an npm library. It has no Python or nested language package. Dependencies are locked with `pnpm-lock.yaml`; formatting, linting, strict type checking, workerd-backed Container and persistence tests, coverage, and a Wrangler dry-run build are reproducible through package scripts and CI.

`src/worker-configuration.d.ts` is Wrangler-generated but contains only the project binding declarations. The complete Workers Runtime declarations stay in the pinned `@cloudflare/workers-types` dependency under `node_modules` rather than adding thousands of generated lines to this repository.

## License

Apache License 2.0. See `LICENSE`.

Chinese documentation is available in `README.zh-CN.md`.
