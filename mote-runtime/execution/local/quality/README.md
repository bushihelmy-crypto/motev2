# Quality gates

Local Execution uses a deterministic Rust gate modeled after the repository's
Kernel and persistence projects:

- `make check` runs structure, toolchain, formatting, Clippy, architecture,
  complexity, documentation, tests, build, and package checks.
- `make security` runs the repository secret baseline and cargo-deny policy.
- Unit and integration tests are separate targets and never require live MCP,
  Skill, or Hub credentials.
- Architecture tests preserve the Kernel-to-Invocation-to-local ingress,
  config-driven `local -> remote` decision, and Invocation transport boundary.
- The complexity ratchet measures production `src/` only and excludes tests,
  build output, and quality scripts.

The scaffold intentionally has no runtime dependencies. Adding one requires a
first consumer, a reviewed owner boundary, lockfile updates, and a cargo-deny
review.
