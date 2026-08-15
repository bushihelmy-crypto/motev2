# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project intends to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once its public API stabilizes.

## [Unreleased]

### Changed

- **Breaking:** `mote_kernel.execution` now exports only the `Graph` facade; executor, session, request/result,
  topology, and state transition types remain internal owner-module contracts.
- `Graph.run()` now rejects invalid execution limits before compilation or any authoritative transition, and
  commit consumers narrow typed node results through `Graph.SuccessResult`, `Graph.FailureResult`, and
  `Graph.InterruptResult` without importing internal modules.
- Public execution failures are available through the `Graph` namespace as `Graph.Error`,
  `Graph.ValidationError`, `Graph.SnapshotMismatchError`, and `Graph.ExecutionLimitError`.
- **Breaking:** Graph nodes now implement an async `Node` protocol, and callers must `await step_graph()`.
  Graph execution is async-only and does not provide a synchronous compatibility path.
- Concurrent nodes now share one immutable input snapshot, which node implementations must treat as read-only.

### Added

- The `Graph` builder and its single async `run()` path, including per-transition commit confirmation and
  selective failure, interrupt, and skip resume inputs.
- Initial repository, packaging, quality, testing, and community infrastructure.
