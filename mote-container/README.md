# Mote Container

`mote-container` is the registration and invocation boundary for the concrete containers in which Mote Agents run. It lets `mote-control` address local, Cloudflare, Docker, and future container implementations uniformly without moving Agent identity, lineage, placement, or flow semantics into this project.

Ownership remains explicit:

- `mote-control` owns Agent identity, lineage, placement, and lifecycle authority.
- `mote-container` owns container registration, lookup, and uniform invocation.
- `mote-kernel` owns Agent creation and flow semantics inside a selected container.
- `mote-infra/persistence` owns concrete persistence and transaction mechanisms selected through Kernel Ports.
- Product surfaces remain the user-facing boundary.

Container placement and persistence placement are independent configuration axes. A Cloudflare Container may expose Durable Object storage to the Port resolver, but the same Container may use a remote persistence backend; `mote-container` neither selects nor implements `Commit`.

Current layout:

```text
mote-container/
├── cloudflare/
│   ├── python/    Python Worker and Durable Object container
│   └── ts/        TypeScript Worker and Durable Object container
├── docker/        reserved Docker container implementation
└── local/         reserved local container implementation
```

The project is pre-alpha. The Cloudflare subprojects currently provide deployable container scaffolds; the shared registration contract will be introduced with its first `mote-control` consumer and matching conformance cases.
