# Mote Gateway engineering rules

## Scope and ownership

- This project owns outbound model inference only: text, image, audio, music,
  video, embeddings, reranking, streaming, realtime, and asynchronous model
  operations.
- Router selects a model. Gateway must not select models, services, protocols,
  fallback candidates, prices, users, budgets, or agent-flow steps.
- Model-native tool calling is an inference capability. Definitions, choices,
  calls, argument deltas, and tool-result messages belong at the model boundary;
  MCP discovery, tool registration, tool execution, and agent loops do not.
- `mote-infra/invocation` owns inbound invocation contracts and transports.
  `src/upstream` owns only outbound model-network mechanics.
- Persistence is supplied through a narrow port. Gateway does not create a
  database, migration system, billing ledger, or user-governance store.

## Architecture

- Keep model, protocol, and service as independent dimensions. Combine them
  once in an immutable call plan after deterministic admission.
- `src/api` is the only provider-neutral DTO boundary. Do not duplicate the
  same request/result/event shape in protocol or connector packages.
- Protocol adapters encode/decode wire values. Service connectors resolve
  targets, credentials, and cloud signatures. Neither may absorb the other's
  owner responsibilities.
- Prompt/context-cache semantics and usage accounting are gateway concerns;
  connection pools, token caches, and connector metadata caches stay with their
  respective transport/connector owners.
- Do not add generic `common`, `shared`, `util`, `utils`, or `helpers` packages.
- Do not add a public field, wire tag, or durable identifier before its owner,
  lifecycle, failure behavior, and first consumer are accepted. Observable
  changes must update `conformance/` in the same change.

## Go engineering

- Use the pinned Go toolchain declared by CI and keep this as one Go module at
  `src/go.mod`.
- Keep imports at package scope, use standard Go naming, and return typed errors
  at owner boundaries. Avoid reflection and untyped maps at internal seams.
- Keep stream lifecycle ownership explicit: every opened stream has one owner,
  one cancellation path, and one finalization path.
- Never log or persist credentials, signed headers, raw authorization material,
  cache keys, or complete user payloads unless an explicitly reviewed artifact
  policy permits it.

## Test and gate policy

- Unit tests live beside the package they protect and are deterministic by
  default. Integration tests live under `src/integration` and run only with
  `-tags=integration`; live provider tests are opt-in and never required by the
  default gate.
- Architecture tests own package layout and dependency direction. They are
  high-signal owner checks, not coverage tests.
- The complexity ratchet records every measured production metric. Any increase
  requires an architecture review and an intentional baseline change; any
  decrease requires lowering the baseline so the improvement cannot regress.
- `make check` is the local equivalent of CI: structure, formatting, vet,
  static analysis, unit tests, integration harness, architecture, complexity,
  module hygiene, and security checks.
- If a tool is unavailable locally, report the exact command and reason; do not
  weaken or silently skip a gate.
