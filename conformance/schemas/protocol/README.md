# Protocol schemas

Add one strict, versioned JSON Schema per cross-language wire or durable
protocol. File names use `<protocol>.v<version>.schema.json`. Schemas reject
unknown fields and variants unless the protocol explicitly defines an extension
map.

`gateway_invocation.v1.schema.json` is the canonical provider-neutral model
invocation contract. Its root validates an invocation request. The named
definitions `#/$defs/request`, `#/$defs/response`, and the profile-specific
`llm_*`/`media_*` definitions are used by the conformance runner to validate
the request and terminal response positions in a vector. It intentionally
does not contain Go method names, provider SDK fields, endpoint credentials,
or transport mechanics.

Do not add placeholder protocol fields. A protocol schema enters this directory only after its owner, identity, lifecycle, failure semantics, and first consumer are confirmed.
