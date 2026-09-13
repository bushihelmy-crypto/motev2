//! Narrow provider capabilities injected into local tool handlers.
//!
//! A port describes how Execution asks another owner to perform one typed
//! operation. It does not contain provider configuration or endpoint
//! selection. Invocation infrastructure supplies the concrete implementation.

mod forwarding;
mod mcp_invocation;
mod skill_invocation;
