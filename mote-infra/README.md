# Mote Infrastructure

`mote-infra` is the infrastructure boundary for concrete adapters used by Mote. It groups persistence and RPC implementations without turning them into Kernel semantics or Container behavior.

```text
mote-infra/
├── persistence/
│   ├── local/                 Rust local persistence adapter
│   └── cloudflare/            Cloudflare Durable Object persistence adapters
│       ├── python/
│       └── ts/
└── rpc/
    ├── http/                  reserved HTTP transport adapter
    ├── grpc/                  reusable gRPC transport adapter
    └── websocket/             reserved WebSocket transport adapter
```

The dependency direction is capability-based:

```text
mote-control  →  mote-container  →  mote-kernel
                         │              │
                         │ ctx/config   ▼
                         └────────── mote-port  ←  mote-infra
                                                   ├── persistence
                                                   └── rpc
```

`mote-container` remains a separate sibling boundary. It allocates or locates a host, prepares the runtime context and Port configuration, and starts Kernel. It does not select a persistence backend or interpret RPC payloads.

The RPC directories are intentionally scaffolds. Transport code and wire contracts will be added with their first concrete consumer and corresponding conformance cases.
