# RPC invocation implementations

This directory contains remote invocation implementations under `mote-infra/invocation`:

```text
rpc/
├── http/
├── grpc/
└── websocket/
```

RPC carries owner-defined protocols; it does not own Agent flow, Resource bindings, or persistence state. No public RPC API is fixed by this scaffold.
