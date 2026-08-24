# Mote Cloudflare Python Persistence

This project is the Cloudflare Durable Object SQLite persistence adapter under `mote-infra/persistence`. It owns the concrete SQL, schema, serialization, migration, and transaction implementation behind Kernel persistence Ports.

It is intentionally separate from `mote-container/cloudflare/python`:

- Container owns the Worker and Durable Object entry points, registration, lookup, invocation, and deployment bindings.
- Kernel owns Agent flow semantics and the persistence Port contract.
- This package implements that Port with the object-local Cloudflare SQLite storage API.

Container choice and persistence-backend choice are independent. Port configuration constructs this Adapter only when it selects Cloudflare object-local SQLite and supplies the Durable Object storage handle. The same Cloudflare Container may instead select a remote backend. This lowest-level package imports neither Kernel nor Container. The `storage: "sqlite"` field remains in the Container's `wrangler.jsonc` because it is Cloudflare deployment metadata; all calls to the SQL and transaction APIs belong here.

The package exposes only `Commit`. It is structurally compatible with Kernel's callable Commit Port without importing `mote-kernel`. It uses the injected Cloudflare Durable Object `storage.sql` and `transactionSync()` APIs and never opens a local SQLite database. Durable state bytes are supplied by the Port configuration's versioned encoder.

## Development

The toolchain is pinned to Python 3.13 and uv 0.12.3. Install dependencies inside this project and run its complete checks with:

```bash
uv sync --locked
make check
```

## Package status

This package is pre-alpha and is bundled into a Cloudflare Python Worker only when selected by Port configuration; it is not currently published to PyPI.

## License

Apache License 2.0. See [LICENSE](LICENSE).
