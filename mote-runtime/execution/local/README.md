# Mote Local Execution Runtime

Kernel reaches this Rust package through the `mote-infra/invocation` ingress.
The package admits each operation, reads the configured execution route, and
then chooses either a local tool handler or a remote capability. The remote
branch is `local -> remote`; socket framing, transport, endpoint resolution,
deadlines, and transport errors remain owned by `mote-infra/invocation`.

The package currently establishes implementation boundaries only. Execution
DTO fields will be added together with the authoritative cross-language
contract under the monorepo `conformance/` directory.

MCP tool calls and Skill reads are execution operations. Their provider
configuration, discovery, and endpoint selection live outside this package;
the route config only selects local handling versus the `local -> remote`
branch. The local runtime receives an injected remote capability; the first
Skill slice is read-only.
