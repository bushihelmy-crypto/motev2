# Protocol schemas

Add one strict, versioned JSON Schema per cross-language wire or durable
protocol. File names use `<protocol>.v<version>.schema.json`. Schemas reject
unknown fields and variants unless the protocol explicitly defines an extension
map.

`gateway_invocation.v2.schema.json` is the current canonical service- and
protocol-neutral model invocation contract. Its root validates an invocation
request. Model addressing uses the bare `base_model` field. The named
definitions `#/$defs/request`, `#/$defs/response`, and the profile-specific
`llm_*`/`media_*` definitions are used by the conformance runner to validate
the request and terminal response positions in a vector. It intentionally
does not contain Go method names, upstream SDK fields, endpoint credentials,
or transport mechanics.

The v1 schema remains as an immutable historical contract and is not the
currently enabled version.

Do not add placeholder protocol fields. A protocol schema enters this directory only after its owner, identity, lifecycle, failure semantics, and first consumer are confirmed.
