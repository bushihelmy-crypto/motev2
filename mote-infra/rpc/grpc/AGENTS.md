# Mote gRPC transport engineering rules

- Reuse the official gRPC runtime and generated-code toolchain for the target language; do not reimplement gRPC framing or HTTP/2.
- Keep channel, deadline, metadata, retry, and streaming mechanics here. Message meaning and ownership remain with the caller's protocol.
- Generated bindings must be derived from an owner-approved schema. Do not define a second private schema in this directory.
- A Persistence adapter may consume a narrow gRPC client, but the Kernel Persistence Port must not mention gRPC types.
- Keep transport errors and generated messages inside the adapter and map them to the owning boundary's typed result.
- Add a conformance case whenever a cross-language observable RPC behavior is introduced.
