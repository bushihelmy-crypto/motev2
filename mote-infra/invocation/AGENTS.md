# Mote Infrastructure Invocation engineering rules

- This is Mote's sole invocation infrastructure owner. Invocation contracts, explicit resolution, local implementations, and RPC implementations belong here; do not create a parallel invoker in Kernel, Resource, Runtime, or Persistence.
- Keep contracts narrow and typed. Kernel and other callers see their owner-defined Port and typed result, not transport, resolver, or backend-specific values.
- `contract/` owns only invocation-level contracts; shared observable cross-language schemas remain in `conformance/`.
- `resolver/` binds an explicitly configured capability to one implementation. Do not add hidden discovery, fallback, or mutable registries.
- `local/` and `rpc/` are implementation choices behind the same owner boundary. RPC is not a parallel infrastructure owner.
- Keep wire values and serialization inside `rpc/`; do not import Kernel flow modules, mutate Kernel state, or own persistence transactions.
- Do not add a universal invocation abstraction, endpoint schema, retry policy, or compatibility path without a concrete consumer and matching contract.
- Read the nearest implementation `AGENTS.md` before changing a concrete child.
