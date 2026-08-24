# Mote Cloudflare Python Container engineering rules

## Architecture

- Implement this project in Python 3.13 against Cloudflare Python Workers and Durable Objects APIs.
- One logical Agent maps to one Durable Object identity. Never place cross-Agent mutable state in one object.
- `mote-control` owns Agent identity, lineage, placement, and registration. This Container must consume Control-issued identities rather than minting them.
- `mote-kernel` owns Agent flow semantics. This Container may call its public contracts but must not copy or reinterpret Kernel behavior.
- Do not invent an Agent request protocol, durable schema, identity encoding, or Product route before a real Kernel consumer and matching conformance contract exist.
- State that must survive eviction, restart, or deployment goes through the backend independently selected by Kernel Port configuration. Python instance fields are reconstructible caches only.
- Cloudflare Container selection does not imply Cloudflare SQLite persistence. The Container may expose `ctx.storage` as a platform capability, while Port configuration may instead select a remote persistence backend.
- New Durable Object classes declare the SQLite storage binding required by the Cloudflare deployment in `wrangler.jsonc`.
- If selected, raw Cloudflare SQL, schema, and transaction code belong to `mote-infra/persistence/cloudflare/python`, not this Container project. Do not call the Durable Object SQL API here.

## Engineering

- Keep the Worker entry point narrow and typed. Product HTTP routing and Kernel flow behavior do not belong in it.
- Use only Python packages supported by Cloudflare's Pyodide or PyEmscripten runtime.
- Pin `workers-py`, `workers-runtime-sdk`, Wrangler, uv, and pnpm; commit both lock files.
- Install Python and Node dependencies inside this project. Do not create a nested Git repository.
- Add deterministic host tests for container behavior and runtime-backed tests for Durable Object identity, eviction, alarms, and concurrency as those mechanisms are introduced.
- Python Workers are currently beta; keep the `python_workers` compatibility flag and compatibility date explicit.
- Preserve user changes and inspect the relevant Git diff before editing.
- Before handoff, run `make check` here and affected monorepo pre-commit hooks, or report precisely which checks could not run.
