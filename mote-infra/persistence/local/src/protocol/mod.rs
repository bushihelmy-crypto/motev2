//! Candidate home for persistence-specific mapping of conformance-owned values.
//!
//! Generic RPC transport and listeners belong to `mote-infra/invocation/rpc`. If this
//! boundary is retained, it may only validate persistence-owned values and
//! explicitly convert them into internal values. Persistence does not
//! independently own a wire schema or a durable state transition.
