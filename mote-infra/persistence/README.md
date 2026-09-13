# Mote Infrastructure Persistence

`mote-infra/persistence` owns Mote's portable local and remote storage implementations. It implements durable storage and transaction mechanisms behind Kernel-owned persistence Ports; it does not own Agent state-transition semantics or invocation transport.

Container and Persistence are independent choices:

```text
Container config ──▶ local / Docker / Cloudflare host

Kernel Port config ──▶ local Rust / remote backend / Cloudflare DO SQLite
```

A Cloudflare Container can use object-local Durable Object SQLite or a remote store. Port configuration still makes that choice.

Cloudflare object-local persistence is the platform-bound exception to this directory layout. Its TypeScript Adapter is co-located at `mote-resource/container/cloudflare/src/persistence.ts`, in the same deployed Worker project as the Durable Object that receives `ctx.storage`. The handle stays inside that project and never crosses the Kernel Port or Invocation contract. There is no separate Cloudflare Persistence project and no Python Cloudflare implementation.

Current layout:

```text
mote-infra/persistence/
└── local/                 Rust local and host-native implementation
```

All projects are pre-alpha. Each child project owns its dependencies, lockfiles, tests, and release artifact.
