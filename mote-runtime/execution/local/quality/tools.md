# Quality tools

The production package intentionally has no third-party runtime dependency.
CI installs only the following pinned external quality tool:

| Tool | Version | Purpose |
| --- | --- | --- |
| `cargo-deny` | `0.19.7` | dependency advisories, licenses, bans, and sources |

Rustup supplies `rustfmt`, `clippy`, and `rustdoc` from the pinned toolchain.
