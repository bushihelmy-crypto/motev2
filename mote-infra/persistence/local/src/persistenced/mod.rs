//! Candidate standalone local Persistence service composition boundary.
//!
//! Configuration loading, concrete adapter construction, telemetry, and
//! graceful shutdown may converge here. RPC listeners belong to the sibling
//! `mote-infra/rpc` boundary. No executable entry point or stable module
//! boundary is inferred until the first complete storage and protocol slice
//! is designed.
