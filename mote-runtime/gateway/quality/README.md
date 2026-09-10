# Quality gates

The gateway follows the same ratchet principle as `mote-kernel`, expressed in
Go-native tooling:

- `make test-unit` runs deterministic package tests with `-race` and produces a
  machine-readable coverage profile.
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

The current baseline measures the scaffold itself. It deliberately excludes
test infrastructure and the process shell, so adding implementation code makes
the ratchet fail until the owner review records the new intentional complexity.
