# Mote Cloudflare TypeScript Persistence

This project is the TypeScript Cloudflare Durable Object persistence implementation under `mote-infra/persistence`. It owns concrete `storage.sql`, schema, serialization, migration, and transaction behavior.

It is intentionally separate from `mote-container/cloudflare/ts`:

- Container owns Worker and Durable Object entry points, registration, lookup, invocation, and deployment bindings.
- Upper layers own Agent transition semantics and the observable persistence contract.
- This package implements that contract with the object-local Cloudflare SQLite storage API.

Container choice and persistence-backend choice are independent. Port configuration constructs the sole exported `Commit` callable only when it selects Cloudflare object-local SQLite and supplies the Durable Object storage handle. The same Cloudflare Container may instead select a remote backend. This package imports no upper layer. The `storage: "sqlite"` field remains in the Container's `wrangler.jsonc` because it is Cloudflare deployment metadata; all calls to `storage.sql` and `transactionSync()` belong here. Tests use workerd's real Durable Object storage, never host-local SQLite.

No other public API is exported. State access and encoding functions are injected when `Commit` is constructed so this lowest layer does not depend on a Kernel type.

## Development

Node 24 is the primary development version. CI also checks supported Node releases, and pnpm is pinned in `package.json`.

```bash
pnpm install --frozen-lockfile
pnpm run check
```

## Package status

This package is private and is bundled into a Cloudflare Worker only when selected by Port configuration; it is not published independently.

## License

Apache License 2.0. See [LICENSE](LICENSE).
