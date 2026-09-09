# Local Persistence architecture

The Python Kernel owns Agent flow semantics and the `Commit` Port contract.
This Rust package is the portable local persistence implementation behind that
Port. It may eventually provide durable state, compare-and-swap, atomic commit,
receipts, coordination, migrations, and storage adapters, but it must not
interpret Think, Act, Context, Spawn, or other Agent-flow intent.

```text
Kernel Commit Port
        │
        ▼
mote-infra/persistence/local
        │
        ├── durable       reliability mechanisms and private storage adapters
        ├── protocol      strict mapping of conformance-owned values
        └── persistenced  optional local composition and lifecycle wiring
```

The three source directories are provisional responsibility areas. They are
not public package boundaries until a complete consumer-driven storage slice
and its conformance cases exist. Backend selection remains a caller Port
configuration concern and is independent of the Container that hosts an
Agent.

Persistence owns portable storage and transaction mechanics only. Invocation
contracts, endpoint resolution, framing, RPC listeners, and transport errors
belong to `mote-infra/invocation`; Cloudflare Durable Object SQLite remains in
`mote-resource/container/cloudflare` because only that object receives
`ctx.storage`. Kernel, Container, Control, Product, and Runtime code must not
become dependencies of this package.

Raw SQL handles, storage-engine transactions, generated wire types, and private
backend errors stay behind narrow typed ports. Cross-language observable
values are added only after the root `conformance/` contract is versioned.
