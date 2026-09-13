# Pinned quality tools

The scaffold keeps external quality tools out of the production module. They
are installed into the ignored `.tools/bin` directory by `make tools`:

| Tool | Version | Purpose |
| --- | --- | --- |
| `golangci-lint` | `v2.13.2` | static analysis and lint policy |
| `govulncheck` | `v1.7.0` | Go dependency and standard-library vulnerability scan |

The versions are intentionally explicit in `Makefile`. Updating one requires a
review of its output and a successful `make check`; no quality gate silently
falls back to an unpinned binary.
