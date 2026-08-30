# Architecture

Mote Kernel separates four concerns that agent frameworks commonly merge:

- **Domain flows** define why work proceeds in a particular order.
- **Execution** provides the sole graph compiler and runtime used by every flow.
- **State machines** decide which graph and domain transitions are legal.
- **Ports** supply replaceable I/O capabilities without owning kernel state.

The authoritative snapshot is an `AgentState` composed of independently versioned `GraphState` and `DomainState`. A node produces typed graph and domain commands. Pure reducers calculate a candidate snapshot, the state store commits it atomically, and only a confirmed commit may replace the Python in-memory snapshot.

Every node in one concurrent frontier receives the same immutable input snapshot. Nodes and ports must treat that snapshot as read-only and return typed outcomes instead of mutating it. Kernel does not clone arbitrary domain DTOs; their owner must define them as immutable values.

`mote_kernel.execution.Graph` is the sole public graph composition and execution facade. It is a mutable topology builder until its first `run()`, when it validates and freezes one immutable compiled runtime. The facade never retains a run snapshot, session, or transient output as instance state, so one assembled graph can drive independent runs without becoming a second source of truth. Required ports are validated when an agent flow is assembled. Missing optional ports remove their corresponding nodes when graph definitions are assembled, keeping runtime paths deterministic.

`Graph.run()` starts or continues from an explicitly supplied authoritative `GraphRunState`. Selective failure, interrupt, and skip actions are inputs to that same method; resume is not a second runner. With no commit callback, `run()` applies the pure reducer process-locally. With a callback, it offers every command, reducer candidate, and optional typed node result for atomic graph/domain commit, and advances only when the callback returns the exact candidate. This is a commit boundary, not a concrete store or durability claim.

## Graph frontier execution

`GraphRunState` is the sole durable truth for frontier settlement, resource ownership, and the active execution token. One atomic
`ClaimGraphExecution` transition installs a token-only lease and, when needed, the initial `ResourceSnapshot`.

Inside the facade, `GraphExecutor.execute()` is the only supported session creation path. It consumes the prepared claim linearly and issues a
single-consumer `GraphExecutionSession`; the internal session contract is a non-constructible protocol. Each `next(authoritative_state)` call
requires the exact successor of the preceding reducer command and yields at most one typed node completion with one `SettleGraphNode` command. Concurrent
`next()` calls fail closed before reaching the scheduler, and `aclose()` is idempotent and waits for live tasks to stop.
Cancelling `next()` runs close before propagating cancellation; cancelling that same task again during cleanup cannot interrupt the close.

`SettleGraphNode` atomically records that node's settlement, releases its resources, and advances deterministic resource waiters in one
new `GraphRunState`. Resource requirements only affect which pending nodes the single scheduler may select. Once a caller applies a
settlement and acknowledges the successor state, a newly admitted waiter is submitted immediately even when another typed sibling
completion is already queued; an observed ordinary error instead stops all new activations.

The final node settlement persists a stable `RUNNING + SETTLED` frontier first. Routing is resolved only from that persisted barrier and
then produces a standalone `AdvanceGraphFrontier` or `CompleteGraphFrontier` transition. Session queues and task handles are transient;
they are not a store, retry policy, exactly-once guarantee, or second durable state model.

This document records the stable architectural direction. Authoritative public contracts will be documented alongside their implementation.
