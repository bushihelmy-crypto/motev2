# Mote Kernel engineering rules

## Architecture

- `Role` is the sole default public entry point and composition owner.
- `execution` is the only graph execution engine; domain packages define topology but never create private runners.
- `GraphState` records recoverable execution position. `DomainState` records established business facts. They evolve separately and commit atomically as one `AgentState`.
- State transitions are pure. Services and tools return typed results and commands; they never mutate state directly.
- The persistent state store is authoritative. Update durable state first and replace the Python memory snapshot only after a confirmed commit.
- Concrete capabilities enter through narrow typed ports. Missing required ports fail assembly; missing optional ports remove their steps when the graph is assembled.
- Do not create compatibility aliases, duplicate execution paths, hidden mutable state, or generic `utils`, `common`, `shared`, or `helpers` packages.

## Engineering

- Use Python 3.11 or newer and strict type checking.
- Keep imports at module scope. Resolve cycles by correcting ownership or extracting a narrow protocol.
- Do not use `Any`, bare dictionaries, reflection, or string discriminators at internal boundaries.
- Preserve user changes. Inspect Git status and relevant diffs before editing.
- Add deterministic tests for every state transition, recovery boundary, and public behavior.
- Before handoff, run `make check` in this directory and repository-level pre-commit checks from the monorepo root, or report precisely which checks could not run.
