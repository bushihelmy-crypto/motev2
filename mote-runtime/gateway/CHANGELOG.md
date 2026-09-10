# Changelog

All notable changes to Mote Gateway will be documented here. The project
follows Keep a Changelog conventions and will use Semantic Versioning once the
first public API is released.

## [Unreleased]

### Added

- Initial Go project scaffold with explicit model, protocol, service, cache,
  transport, receipt, and telemetry ownership boundaries.
- Layered unit/integration test entry points and architecture/complexity/quality
  gate configuration.

No model wire protocol or durable DTO is released by this scaffold.

### Changed

- Replaced ambiguous provider vocabulary at the public boundary with explicit
  service, protocol, and upstream identity names.
- Narrowed monetary observation data to optional upstream-service-reported cost facts;
  Gateway does not estimate costs or own pricing and customer billing policy.
