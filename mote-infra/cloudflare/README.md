# Mote Infra for Cloudflare

This project is the pure TypeScript Cloudflare implementation of Mote Infra. It provides the deployment and testing foundation for running one logical Agent in one SQLite-backed Durable Object without moving Agent flow semantics out of the Kernel.

The project is in its bootstrap phase. The Durable Object class and binding are deployable, but no Agent request protocol, persistent state schema, or Product route has been fixed yet. Those interfaces will be introduced by the first consumer-driven vertical slice and its conformance cases.

## Runtime model

- `AgentDurableObject` is the Cloudflare-owned execution and storage container for one logical Agent.
- A stable Agent identity will select one Durable Object identity when the shared identity contract is defined.
- State that must survive eviction or deployment will use the object's private, strongly consistent SQLite storage.
- Multi-statement SQL changes use `transactionSync()`; Agent flow transitions remain owned by `mote-kernel`.
- The default Worker currently returns `404`, and the Durable Object returns `501`, so the scaffold does not accidentally establish a Product API.

The Durable Object namespace is declared with Cloudflare's current declarative `exports` configuration and `storage: "sqlite"`. The older `migrations[].new_sqlite_classes` form is intentionally not used for this new Worker.

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
