# gRPC transport

This directory is the reusable gRPC transport adapter for Mote infrastructure.

It may provide gRPC clients, servers, channel setup, deadlines, metadata, and generated-code integration. It must not become the owner of Agent flow, lineage, persistence transactions, or Container/Embodiment placement semantics.

Protocol ownership stays with the caller:

- Control-plane RPC schemas belong to `mote-control` and its conformance contracts.
- Persistence RPC schemas belong to the persistence boundary and shared `conformance/` contracts when they are cross-language.
- Generated language bindings stay local to the consuming implementation.

When a remote Persistence adapter uses gRPC, depend on this transport through a narrow client boundary. Keep the Persistence Port independent from gRPC so the same backend can later use HTTP or WebSocket.
