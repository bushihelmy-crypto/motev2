//! Narrow capability for the `local -> remote` execution branch.
//!
//! The route policy may select this capability when configuration says the
//! operation is remote. The remote implementation uses transport mechanisms
//! owned by `mote-infra/invocation`, which owns framing, endpoint resolution,
//! deadlines, and transport errors. This module does not define a wire DTO or
//! provider configuration schema.
