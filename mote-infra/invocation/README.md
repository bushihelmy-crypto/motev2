# Mote Invocation Infrastructure

`mote-infra/invocation` is Mote's sole invocation infrastructure owner. It groups the narrow invocation contract, explicit implementation resolution, and local or remote implementations without taking ownership of caller semantics or state:

```text
invocation/
├── contract/      narrow typed invocation contracts
├── resolver/      explicit implementation resolution
├── local/         local invocation implementation
└── rpc/           remote invocation implementations
    ├── http/
    ├── grpc/
    └── websocket/
```

Resource resolves a narrow target handle; Invocation selects and executes the configured local or RPC implementation; the caller interprets the typed result. Persistence is the parallel sole storage infrastructure owner and remains responsible for durable state and transaction mechanisms.

These directories currently establish ownership, not a frozen public API. Add concrete types or implementations only with a real caller. Shared observable cross-language schemas remain in `conformance/`; no universal invocation abstraction or compatibility path is implied.
