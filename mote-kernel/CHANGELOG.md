# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project intends to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once its public API stabilizes.

## [Unreleased]

### Changed

- **Breaking:** Logging and Observability now expose only two-stage diagnostic decorators:
  `LoggedNode(sink, ...)(inner)`, `ObservedNode(port, span_factory)(inner)`, and
  `LoggedGraphCommit(sink, ...)(inner)`. The former inner-first constructors, public generic subscripts, and
  `LoggedGraphCommit(inner=None)` fallback are removed; `Graph.run(commit=None)` remains the execution-owned fallback.
- **Breaking:** `LogSinkPort` and `ObservabilityPort` are now async, typed adapters over the shared
  `mote_kernel.invocation.Invocation` seam. They select the best-effort policy at the Port boundary; transport and runtime
  selection remain configuration owned by `mote-infra/invocation`, while core/Hook calls use the strict policy.
- Events now has an invocation-backed `EventPort` for the same explicit best-effort notification policy. It receives only a
  confirmed settlement reference after the atomic persistence commit; durable delivery remains owned by the persistence outbox
  and its dispatcher.
- **Breaking:** `mote_kernel.execution` now exports only the `Graph` facade; executor, session, request/result,
  topology, and state transition types remain internal owner-module contracts.
- `Graph.run()` now rejects invalid execution limits before compilation or any authoritative transition, and
  commit consumers narrow typed node results through `Graph.SuccessResult`, `Graph.FailureResult`, and
  `Graph.InterruptResult` without importing internal modules.
- Public execution failures are available through the `Graph` namespace as `Graph.Error`,
  `Graph.ValidationError`, `Graph.SnapshotMismatchError`, and `Graph.ExecutionLimitError`.
- Graph entries are declared with `add_edge(Graph.START, node_id)`, symmetric with `Graph.END`; the builder no
  longer maintains a separate `set_entry()` configuration path.
- **Breaking:** Graph nodes now implement an async `Node` protocol, and callers must `await step_graph()`.
  Graph execution is async-only and does not provide a synchronous compatibility path.
- **Breaking:** `ResourceDefinition` now contains only `resource_id`; its former `order` argument and
  attribute are removed. The containing `GraphDefinition.resources` tuple is the sole source of static
  resource-acquisition order. Callers that construct internal graph definitions must put resources in
  their intended order; no legacy alias or compatibility wrapper is retained.
- Concurrent nodes now share one immutable input snapshot, which node implementations must treat as read-only.

### Added

- The `Graph` builder and its single async `run()` path, including per-transition commit confirmation and
  selective failure, interrupt, and skip resume inputs.
- Initial repository, packaging, quality, testing, and community infrastructure.
