# Quality gates

Local Infrastructure Persistence uses the same deterministic Rust gate as the
Local Execution package:

- `make check` runs structure, toolchain, formatting, Clippy, architecture,
  complexity, documentation, tests, build, Cargo hygiene, license metadata,
  and package checks.
- `make security` runs the repository secret baseline and cargo-deny policy.
- Unit and integration tests are separate and never require live databases,
  RPC services, providers, or credentials.
- Architecture checks preserve the Kernel-owned Port boundary and keep
  invocation transport, Container selection, and Agent-flow semantics out of
  persistence.
- The complexity ratchet measures production `src/` only and excludes tests,
  build output, and quality scripts.

The project is intentionally dependency-free during bootstrap. A new runtime
dependency needs a concrete consumer, a reviewed owner boundary, lockfile
updates, and a cargo-deny review.
