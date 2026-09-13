# Local Execution architecture

Kernel submits every tool execution to `mote-infra/invocation`. Invocation
delivers the request to this package's local implementation. `local` is then
the execution policy boundary: after admission it reads the configured route
and chooses a local handler or a remote capability.

```text
Kernel ExecutePort
        │
        ▼
mote-infra/invocation
  (Kernel ingress, framing, and explicit resolution)
        │
        ▼
execution/local invocation boundary
        │
        ├── admission
        └── route config
              ├── local catalog / dispatch
              │     ├── process and workspace tools
              │     ├── MCP call handler
              │     └── Skill read handler
              └── remote capability
                    ▼
              execution/remote
                (remote execution implementation; transport
                 mechanics remain in mote-infra/invocation)
```

The route config controls only whether an admitted operation is handled in this
process or sent from `local` to `remote`. `local` does not implement sockets,
framing, retries, or endpoint resolution. The remote branch uses the remote
execution capability and its transport adapter from `mote-infra/invocation`.
MCP and Skill handlers receive narrow provider capabilities. Their schemas,
discovery, Hub registry, Agent flow state, and persistence transactions remain
outside this package. Skill mutation/invocation is not part of the first slice.
