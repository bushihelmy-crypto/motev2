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
- Added an immutable model catalog with operation-scoped capabilities,
  model defaults, validated Kernel overrides, and deterministic filtering of
  known optional generation parameters unsupported by the selected model.
  Supported numeric request parameters are clamped to model-owned bounds.
- Unified the effective `max_output_tokens` target default at 4096; models with
  a known smaller output ceiling use that ceiling as the effective default.
- Added built-in Embedding model capabilities with text/image/audio/video
  input declarations, fixed and adjustable vector dimensions, and the same
  deterministic filtering and clamping rules. This does not publish an
  Embedding invocation DTO.
- Reworked the model catalog into a checked-in static seed containing only
  model-owned facts. External source importers and their provenance/state are
  no longer part of Gateway; a future CRUD owner will be the single write
  path.
