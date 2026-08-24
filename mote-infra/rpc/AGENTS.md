# Mote Infrastructure RPC engineering rules

- This boundary owns transport mechanics such as HTTP and WebSocket clients or listeners.
- Keep transport values and serialization at the wire boundary; do not import Kernel flow modules or mutate Kernel state.
- Business ownership remains with the caller: Control owns control-plane semantics, Kernel Ports own capability contracts, and `conformance/` owns shared observable wire contracts.
- Do not add a universal RPC abstraction, endpoint schema, or retry policy without a concrete consumer and matching contract.
- A persistence adapter may use a transport implementation when its selected backend is remote, but transport must not depend on persistence semantics.
- Read the nearest protocol/language `AGENTS.md` before changing a concrete implementation.
