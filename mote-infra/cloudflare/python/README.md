# Mote Cloudflare Python Infra

This project is the Python implementation of Mote Infra for Cloudflare. It provides the deployment foundation for running one logical Agent in one SQLite-backed Python Durable Object while keeping Agent flow semantics in `mote-kernel`.

The project is in its bootstrap phase. The Python Worker and Durable Object class are configured, but no Agent protocol, persistent state schema, Product route, or Kernel integration has been fixed. Those boundaries will be introduced by the first consumer-driven vertical slice and its conformance cases.

## Runtime model

- `Default` is the public Python Worker entry point.
- A stable Agent identity will select one `AgentDurableObject` when the shared identity contract exists.
- The Durable Object will call Python Kernel contracts in-process and implement their Infra ports with Cloudflare storage.
- State that must survive eviction or deployment belongs in the object's private, strongly consistent SQLite database.
- The Worker currently returns `404`, and the Durable Object returns `501`, so the scaffold does not establish a provisional API.

The Worker uses Cloudflare's `python_workers` compatibility flag and declarative Durable Object `exports` configuration with `storage: "sqlite"`. Python Workers are currently beta.

## Development

The pinned toolchain uses Python 3.13, uv 0.12.3, pnpm 10.30.3, and Wrangler 4.125.0. Python dependencies are installed in `.venv`; Wrangler is installed in this project's `node_modules`.

```bash
uv sync --locked
pnpm install --frozen-lockfile
make check
```

Run the Worker locally with:

```bash
uv run pywrangler dev
```

Build the deployment bundle without publishing it:

```bash
make worker-build
```

Deploy only after authenticating Wrangler and confirming the intended Cloudflare account:

```bash
make deploy
```

## Kernel integration

The first Kernel integration will add `mote-kernel` as a real Python dependency and inject a Cloudflare-backed Infra port. Kernel-to-Infra calls will remain ordinary in-process Python calls inside the Durable Object; only Worker-to-Durable-Object communication crosses Cloudflare RPC.

## Package status

This package is pre-alpha. It builds as a typed Python package for verification, while its release artifact is a deployed Cloudflare Worker rather than a PyPI publication.

## License

Apache License 2.0. See [LICENSE](LICENSE).

Chinese documentation is available in [README.zh-CN.md](README.zh-CN.md).
