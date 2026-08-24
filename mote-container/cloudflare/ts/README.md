# Mote Container for Cloudflare

This project is the pure TypeScript Cloudflare Container implementation for Mote. It provides the deployment and testing foundation for running one logical Agent in one Durable Object while keeping Agent identity and lineage in `mote-control`, Agent flow semantics in `mote-kernel`, and persistence-backend selection in Port configuration.

The project is in its bootstrap phase. The Durable Object class and binding are deployable, but no Agent request protocol, persistent state schema, or Product route has been fixed yet. Those interfaces will be introduced by the first consumer-driven vertical slice and its conformance cases.

## Runtime model

- `AgentDurableObject` is the Cloudflare container for one logical Agent.
- A Control-issued stable Agent identity will select one Durable Object identity when the shared identity contract is defined.
- The Container will call Kernel contracts without interpreting Agent flow semantics.
- Durable state will pass through the `Commit` backend selected by Port configuration. That backend may use object-local Cloudflare SQLite or a remote store.
- The default Worker currently returns `404`, and the Durable Object returns `501`, so the scaffold does not accidentally establish a Product API.

The Durable Object namespace is declared with Cloudflare's current declarative `exports` configuration and `storage: "sqlite"`. This exposes an optional platform storage capability; it does not select the persistence backend. If Port configuration selects object-local storage, SQL, schema, and transaction code come from `mote-persistence/cloudflare/ts`. The older `migrations[].new_sqlite_classes` form is intentionally not used for this new Worker.

Container and persistence choices are orthogonal. This package hosts the selected Kernel runtime and exposes platform capabilities, but it does not import, select, or construct a persistence implementation.

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

This package is private because its release artifact is a deployed Cloudflare Worker, not an npm library. Dependencies are locked with `pnpm-lock.yaml`; formatting, linting, strict type checking, workerd-backed tests, coverage, and a Wrangler dry-run build are reproducible through package scripts and CI.

`src/worker-configuration.d.ts` is Wrangler-generated but contains only the project binding declarations. The complete Workers Runtime declarations stay in the pinned `@cloudflare/workers-types` dependency under `node_modules` rather than adding thousands of generated lines to this repository.

## License

Apache License 2.0. See `LICENSE`.

Chinese documentation is available in `README.zh-CN.md`.
