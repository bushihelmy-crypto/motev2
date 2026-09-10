# Mote Infrastructure

`mote-infra` has two parallel infrastructure boundaries. `invocation/` is the only place for invocation contracts, resolution, and local or remote implementations; `persistence/` owns portable local and remote storage implementations. Neither owns Kernel flow semantics or Resource facts.

Tool execution follows one fixed ingress: Kernel calls `invocation/`, which
delivers the request to `mote-runtime/execution/local`. Local Execution chooses
local handling or the `local -> remote` branch from immutable route
configuration; Invocation continues to own the target resolution and
transport mechanics for that branch.

Cloudflare Durable Object SQLite is the deliberate platform-bound exception: its Adapter lives in `mote-resource/container/cloudflare` because only that deployed Durable Object receives `ctx.storage`. It still satisfies the same Kernel-owned persistence Port and is selected independently from Container hosting.

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
    └── local/                 Rust local and host-native persistence
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
