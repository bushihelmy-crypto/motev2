# Local Execution engineering rules

- This package is reached through `mote-infra/invocation` after Kernel submits
  an execution request. It owns the local Execution runtime: admission,
  configuration-driven local-versus-remote routing, local tool handlers,
  workspace/process safety, and execution-owned DTOs.
- The route decision is made here from an immutable execution route config.
  The local branch uses this package's handlers; the remote branch delegates
  to an injected remote capability. A route decision is not endpoint
  discovery and must not inspect Hub/provider registries.
- `mote-infra/invocation` owns framing, Unix/RPC transports, endpoint
  resolution, deadlines, transport errors, and the Kernel-to-local ingress.
  Do not add a second socket or resolver implementation here.
- MCP and Skill provider configuration schemas, discovery, and provider
  endpoint selection belong to their owning configuration/Hub boundary. This
  package may own the typed call handlers and choose the configured remote
  branch; the first Skill operation is read-only.
- Cross-language DTO and observable behavior changes must update the root
  `conformance/` contract and vectors in the same change.
- Do not add hidden fallback, mutable global registries, unbounded output
  buffers, or a second persistence/state owner.
- Keep generated wire code at the transport boundary. Durable or local tool
  logic must depend on narrow typed values rather than generated transport
  types.
- Every new production module needs an owner, module documentation, unit or
  integration coverage, and an intentional complexity-baseline update.
- Run `make check` before handoff. Run `make security` when dependencies or
  credentials-related files change.
