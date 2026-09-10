//! Immutable catalog of local tool definitions and handlers.
//!
//! The catalog contains local handlers only. It never discovers remote
//! providers or silently falls back to a similarly named tool. The route
//! policy decides whether the catalog is used or a remote forwarding
//! capability is selected.
