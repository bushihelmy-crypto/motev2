# Mote Cloudflare Python Container

This project is the Python Cloudflare container implementation for Mote. It provides the deployment foundation for running one logical Agent in one Python Durable Object while keeping Agent identity and lineage in `mote-control`, Agent flow semantics in `mote-kernel`, and persistence-backend selection in Kernel Port configuration.

The project is in its bootstrap phase. The Python Worker and Durable Object class are configured, but no Agent protocol, persistent state schema, Product route, or Kernel integration has been fixed. Those boundaries will be introduced by the first consumer-driven vertical slice and its conformance cases.

## Runtime model

- `Default` is the public Python Worker entry point.
- A Control-issued stable Agent identity will select one `AgentDurableObject` when the shared identity contract exists.
- The Durable Object will call Python Kernel contracts in-process.
- Durable state will pass through the `Commit` backend selected by Port configuration. That backend may use object-local Cloudflare SQLite or a remote store.
- The Worker currently returns `404`, and the Durable Object returns `501`, so the scaffold does not establish a provisional API.

The Worker uses Cloudflare's `python_workers` compatibility flag and declarative Durable Object `exports` configuration with `storage: "sqlite"`. This setting exposes an optional platform storage capability; it does not select the persistence backend. If Port configuration selects object-local storage, SQL, schema, and transaction code come from `mote-infra/persistence/cloudflare/python`. Python Workers are currently beta.

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

## Kernel and persistence Port configuration

The first vertical integration will add `mote-kernel` as a real Python dependency. Kernel Port configuration will independently resolve the `Commit` backend. When it selects `mote-infra/persistence/cloudflare/python`, the resolver supplies the Durable Object storage capability; when it selects a remote backend, no local SQL path is used. This Container hosts Kernel and exposes platform capabilities, but it does not import, select, or construct a persistence implementation.

## Package status

This package is pre-alpha. It builds as a typed Python package for verification, while its release artifact is a deployed Cloudflare Worker rather than a PyPI publication.

## License

Apache License 2.0. See [LICENSE](LICENSE).

Chinese documentation is available in [README.zh-CN.md](README.zh-CN.md).
