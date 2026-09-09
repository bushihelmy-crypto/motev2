//! Dispatch of admitted requests according to the execution route config.
//!
//! The local branch selects a handler from the local catalog; the remote
//! branch delegates to a narrow remote capability. This module does not
//! discover providers or resolve an endpoint.
