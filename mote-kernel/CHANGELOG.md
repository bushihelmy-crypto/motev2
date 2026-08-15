# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project intends to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once its public API stabilizes.

## [Unreleased]

### Changed

- **Breaking:** Graph nodes now implement an async `Node` protocol, and callers must `await step_graph()`.
  Graph execution is async-only and does not provide a synchronous compatibility path.
- Concurrent nodes now share one immutable input snapshot, which node implementations must treat as read-only.

### Added

- Initial repository, packaging, quality, testing, and community infrastructure.
