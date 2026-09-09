# Wire vectors

Strict encode/decode acceptance and rejection vectors belong here. Include
unknown version, unknown tag, missing field, extra field, and wrong primitive
cases for every protocol.

The `gateway_invocation.*.json` vectors exercise the model invocation boundary
across modalities and delivery modes. Positive vectors validate `input` against
the protocol schema and compare the typed `expect.value` observation; negative
vectors deliberately carry an invalid candidate and assert the stable
rejection code. They are deterministic and contain no provider credentials or
live endpoints.
