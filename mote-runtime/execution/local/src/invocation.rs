//! Invocation-infrastructure-facing local execution entry point.
//!
//! The eventual handler receives an invocation delivered by
//! `mote-infra/invocation` after Kernel submission. It performs
//! execution-domain admission, reads the route config, and chooses exactly one
//! local operation or one `local -> remote` operation. Socket framing and
//! address resolution do not belong here.
