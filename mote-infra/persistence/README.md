# Mote Infrastructure Persistence

`mote-infra/persistence` is Mote's sole storage infrastructure owner. It contains the concrete backends behind persistence Ports and owns durable storage and transaction mechanisms, while `mote-kernel` owns Agent state-transition semantics and the callable `Commit` contract. Invocation contracts, resolution, and local/RPC implementations live in the parallel `mote-infra/invocation` boundary.

Container and Persistence are independent choices:

```text
Container config ──▶ local / Docker / Cloudflare host

Kernel Port config ──▶ local Rust / Cloudflare DO SQLite / remote backend
```

A Cloudflare Container can therefore use object-local Durable Object SQLite or a remote store. The Container only exposes runtime capabilities such as `ctx.storage`; Port configuration selects the backend and supplies the selected implementation's constructor inputs.

Dependency direction stays one-way. Persistence implementations do not import Kernel, Container, Control, Product, or Runtime packages. The Cloudflare Python and TypeScript packages expose only `Commit` and accept the Durable Object storage handle when that backend is selected.

Current layout:

```text
mote-infra/persistence/
├── local/                 Rust local and host-native implementation
└── cloudflare/
    ├── python/            Python Durable Object SQLite `Commit`
    └── ts/                TypeScript Durable Object SQLite `Commit`
```

All projects are pre-alpha. Each child project owns its dependencies, lockfiles, tests, and release artifact.
