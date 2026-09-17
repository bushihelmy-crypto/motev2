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
  model defaults and deterministic filtering of known optional generation
  parameters unsupported by the selected model.
  Supported numeric request parameters are clamped to model-owned bounds.
- Unified the effective `max_output_tokens` target default at 4096; models with
  a known smaller output ceiling use that ceiling as the effective default.
- Added Embedding model capabilities with text/image/audio/video
  input declarations, fixed and adjustable vector dimensions, and the same
  deterministic filtering and clamping rules. This does not publish an
  Embedding invocation DTO.
- Reworked the model catalog around the typed `ports.ModelCatalogSource` read
  boundary. Complete source records are validated and atomically refreshed;
  the former production seed and override paths were removed. The checked-in
  gzip catalog is test data only and is never compiled into production code.
- Extended the unreleased v1 invocation contract with optional LLM reasoning
  preferences (`thinking` and `effort`) and model-derived operation defaults;
  this is an authorized pre-production v1 change, not a second wire version.
- Routed unary, streaming, realtime, and async typed frames through the same
  admission and immutable admitted-request owner. Inbound framing/schema
  validation remains with `mote-infra/invocation`; Gateway receives a typed
  frame and requires explicit bound protocol and service fact descriptors.
  Raw DTO execution helpers were removed rather than retained as compatibility
  wrappers.
- Reduced effective tool, structured-output, and message-modality requirements
  from the typed input before intersecting model/protocol/service facts. Service
  descriptors now declare exact deployable models, and admission rejects an
  undeployable Router selection before any adapter call.
- Canonicalized service/protocol identity validation with the response schema,
  fixed helper/syntax/capability error priority, and limited admission-error
  projection to the pre-adapter boundary.
- Moved concrete protocol, service, and upstream implementations below their
  `internal` owners and removed the neutral capability matrix, standalone plan,
  and global registry scaffolds.
