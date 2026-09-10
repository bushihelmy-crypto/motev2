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

For tool execution, Kernel enters this boundary first. Invocation delivers the
request to `mote-runtime/execution/local`; Local Execution then decides from
immutable configuration whether to use a local handler or take the
`local -> remote` branch. Invocation owns the ingress, explicit target
resolution, and transport mechanics used by that branch, but does not
reinterpret the tool route or inspect provider registries.

For other capabilities, the caller likewise supplies an explicit target or
resolved handle and interprets the typed result. Persistence is the parallel
sole storage infrastructure owner and remains responsible for durable state
and transaction mechanisms.

These directories currently establish ownership, not a frozen public API. Add concrete types or implementations only with a real caller. Shared observable cross-language schemas remain in `conformance/`; no universal invocation abstraction or compatibility path is implied.
