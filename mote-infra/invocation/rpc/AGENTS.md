# Mote RPC invocation engineering rules

- This directory contains remote implementations under the sole `mote-infra/invocation` owner; it is not a parallel RPC owner.
- Keep serialization, connection, listener, deadline, and transport errors at this boundary.
- Protocol meaning remains with the caller, and shared observable cross-language schemas remain in `conformance/`.
- Do not build a universal transport facade, hidden fallback chain, or retry policy without a concrete consumer.
- Read the nearest transport/language `AGENTS.md` before changing an implementation.
