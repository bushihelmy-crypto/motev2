# Mote Infrastructure

`mote-infra` has two parallel concrete infrastructure owners. `invocation/` is the only place for invocation contracts, resolution, and local or remote implementations; `persistence/` is the only place for storage and transaction implementations. Neither owns Kernel semantics or Resource facts.

```text
mote-infra/
├── invocation/
│   ├── contract/              narrow typed invocation contracts
│   ├── resolver/              explicit implementation resolution
│   ├── local/                 local invocation implementation
│   └── rpc/                   remote invocation implementations
│       ├── http/
│       ├── grpc/
│       └── websocket/
└── persistence/
│   ├── local/                 Rust local persistence adapter
│   └── cloudflare/            Cloudflare Durable Object persistence adapters
│       ├── python/
│       └── ts/
```

The dependency direction is capability-based:

```text
mote-control  →  mote-resource/container  →  mote-kernel
                         │                         │
                         │ ctx/config              ▼
                         └──────────────────── mote-port  ←  mote-infra
                                                            ├── invocation
                                                            └── persistence
              └→ mote-resource/embodiment (capability handle)
```

`mote-resource/container` allocates or locates a host, prepares the runtime
context and Port configuration, and starts Kernel. `mote-resource/embodiment`
resolves physical-body capability handles; neither resource boundary selects a
persistence backend nor owns invocation contracts, resolution, or transport.

The invocation directories are ownership scaffolds. Concrete contracts and implementations are added only with a real consumer; cross-language observable schemas remain in `conformance/`.
