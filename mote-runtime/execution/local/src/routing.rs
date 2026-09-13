//! Configuration-driven local-versus-remote route policy.
//!
//! This boundary consumes an immutable execution route configuration after
//! admission. It may select a local catalog handler or an injected remote
//! capability, but it never owns MCP/Skill schemas, provider discovery, Hub
//! registries, socket framing, or endpoint resolution.
