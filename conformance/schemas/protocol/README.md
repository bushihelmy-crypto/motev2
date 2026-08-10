# Protocol schemas

Add one strict, versioned JSON Schema per cross-language wire or durable protocol. File names use `<protocol>.v<version>.schema.json`. Schemas reject unknown fields and variants unless the protocol explicitly defines an extension map.

Do not add placeholder protocol fields. A protocol schema enters this directory only after its owner, identity, lifecycle, failure semantics, and first consumer are confirmed.
