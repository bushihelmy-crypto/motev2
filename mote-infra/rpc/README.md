# Mote RPC Infrastructure

This directory contains transport implementations used by Mote infrastructure adapters. It is deliberately separate from persistence semantics:

```text
rpc/
├── http/          HTTP transport
├── grpc/          gRPC transport
└── websocket/     WebSocket transport
```

The transport layer carries an owner-defined protocol. It does not define Agent flow, lineage, persistence transactions, or Container placement. Those contracts remain with Kernel Ports, Control, Persistence, and Container respectively.

No public RPC API is fixed yet. Add a concrete child package only when a real caller and conformance contract exist.
