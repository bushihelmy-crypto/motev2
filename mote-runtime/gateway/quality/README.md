# Quality gates

The gateway follows the same ratchet principle as `mote-kernel`, expressed in
Go-native tooling:

- `make test-unit` runs deterministic package tests with `-race`, instruments
  every Go package in the module through one `-coverpkg=./...` scope, and
  produces a machine-readable coverage profile. Architecture, complexity, and
  integration test runners remain separate gates; their production statements
  are still part of the module-wide instrumentation scope.
- `make coverage` requires exact 100% statement coverage. The checker merges
  repeated multi-package coverage blocks by source location and rejects any
  block whose aggregate count is zero, so the displayed percentage cannot pass
  merely because it rounded up to `100.0%`. Command entry points and
  `internal/testkit` are included; no production file or owner is excluded to
  improve the number.
- `make test-integration` runs only tests tagged `integration`; external API
  credentials are never implicit.
- `make architecture` runs executable package-layout and dependency-direction
  checks. These checks reject ownerless packages, import cycles, and edges that
  make the protocol/service/transport boundaries collapse.
- `make complexity` runs the AST complexity ratchet. The checked-in baseline is
  exact: increases require a reviewed baseline change, and improvements require
  lowering the baseline.
- `make quality` runs formatting, `go vet`, static analysis, module hygiene,
  secret scanning (when available), and the architecture/complexity gates.

The current baseline measures production owners and deliberately excludes test
infrastructure and the process shell. The intentional change recorded in the
current baseline is the replacement of the invocation forwarding scaffold with
one typed-frame → admission → immutable admitted-request → adapter chain.
Inbound wire decoding/schema validation is owned outside this module. The
measured set also includes exact operation defaulting, reasoning capability
policy, model/protocol/service intersection, complete source-record conversion,
and atomic Catalog refresh. Delivery-specific public methods are only boundary
adapters into that single application chain; they do not own a second
validation or dispatch path.
These are reviewed architectural additions, not a ratchet waiver; any further
metric change must update the baseline and its rationale again. The
metric-by-metric record is in
[`complexity-review.md`](complexity-review.md).
