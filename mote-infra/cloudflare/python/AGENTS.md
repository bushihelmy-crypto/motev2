# Mote Cloudflare Python Infra engineering rules

## Architecture

- Implement this project in Python 3.13 against Cloudflare Python Workers and Durable Objects APIs.
- One logical Agent maps to one Durable Object identity. Never place cross-Agent mutable state in one object.
- `mote-kernel` owns Agent flow semantics. This Infra implementation may call its public contracts but must not copy or reinterpret Kernel behavior.
- Do not invent an Agent request protocol, durable schema, identity encoding, or Product route before a real Kernel consumer and matching conformance contract exist.
- State that must survive eviction, restart, or deployment belongs in Durable Object storage. Python instance fields are reconstructible caches only.
- New Durable Object classes use the SQLite backend declared in `wrangler.jsonc`.
- Multi-statement SQL changes use the Durable Object storage transaction primitive. Do not issue raw `BEGIN`, `COMMIT`, `ROLLBACK`, or `SAVEPOINT` statements.
- Raw SQL storage, Durable Object state, environment bindings, and Cloudflare FFI values stay inside this Infra adapter.

## Engineering

- Keep the Worker entry point narrow and typed. Product HTTP routing and Kernel flow behavior do not belong in it.
- Use only Python packages supported by Cloudflare's Pyodide or PyEmscripten runtime.
- Pin `workers-py`, `workers-runtime-sdk`, Wrangler, uv, and pnpm; commit both lock files.
- Install Python and Node dependencies inside this project. Do not create a nested Git repository.
- Add deterministic host tests for platform-neutral behavior and runtime-backed tests for persistence, transactions, eviction, alarms, and concurrency as those mechanisms are introduced.
- Python Workers are currently beta; keep the `python_workers` compatibility flag and compatibility date explicit.
- Preserve user changes and inspect the relevant Git diff before editing.
- Before handoff, run `make check` here and affected monorepo pre-commit hooks, or report precisely which checks could not run.
