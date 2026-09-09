package api

// Realtime wire fields are declared on LLMInput in inference.go. Keeping the
// operation-discriminated input in one DTO prevents a second configuration
// shape from drifting away from the conformance schema. DuplexSession in
// invocation.go owns live frame delivery; only the finalized LLMResponse
// crosses the runtime boundary.
