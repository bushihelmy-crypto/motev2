# Architecture

Mote Kernel separates four concerns that agent frameworks commonly merge:

- **Domain flows** define why work proceeds in a particular order.
- **Execution** provides the sole graph compiler and runtime used by every flow.
- **State transitions** decide which execution, node-result, and business transitions are legal.
- **Ports** supply replaceable I/O capabilities without owning kernel state.

The current authoritative snapshot is one immutable `GraphRunState`. Today it records the graph-run execution facts
(frontier, settlement, routing, leases, resources, recovery coordinates, and revision). Node/Hook results and business
facts added later must extend this same type; they must not introduce another state model. There is one state owner and
one atomic commit boundary. Modules may group pure transition functions by concern, but they do not define additional
state models or commit paths.

Storage adapters may load execution records and result records separately. They must join them by one
`state_version` / `commit_id` before exposing an immutable in-memory `GraphRunState` projection:

```text
execution loader ─┐
                   ├─ same-version join → GraphRunState → node / Hook
result loader ────┘
```

No independently loaded snapshot is visible to callers. Role configuration remains owned by Role/Config and is not
a second state model inside the Kernel.

Every node in one concurrent frontier receives the same immutable input snapshot. Nodes and ports must treat that snapshot as read-only and return typed outcomes instead of mutating it. Kernel does not clone arbitrary domain DTOs; their owner must define them as immutable values.

`mote_kernel.execution.Graph` is the sole public graph composition and execution facade. It is a mutable topology builder until its first `run()`, when it validates and freezes one immutable compiled runtime. The facade never retains a run snapshot, session, or transient output as instance state, so one assembled graph can drive independent runs without becoming a second source of truth. Required ports are validated when an agent flow is assembled. Missing optional ports remove their corresponding nodes when graph definitions are assembled, keeping runtime paths deterministic.

`Graph.run()` starts or continues from an explicitly supplied authoritative `GraphRunState`. Failure, interrupt, skip,
node-result, and Hook changes are all inputs to the same `GraphRunCommand` path; resume is not a second runner. With no
commit callback, `run()` applies the pure state transition process-locally. With a callback, it offers every command,
candidate, and optional typed node result for one atomic state commit, and advances only when the callback returns the
exact candidate. This is a commit boundary, not a concrete store or durability claim.

## State package and ownership

`src/mote_kernel/state/` is the sole owner of state facts and transitions. The current concrete modules live under
`state/graph_state/`; this is an implementation path, not a second kind of state. Its current contract is:

- `state/graph_state/model.py` defines the immutable `GraphRunState` and its value records;
- `state/graph_state/command.py` defines the closed, typed `GraphRunCommand` union;
- `state/graph_state/reducer.py` is the single pure dispatch entry point (`reduce_graph_run`);
- validation, identity, frontier/resource/routing values, and transition results remain implementation modules under
  the same `state` owner.

These modules are an implementation layout, not separate runtime states. A node or Hook returns typed results and
commands; only `reduce_graph_run` produces the next `GraphRunState`. No flow package, execution session, or extension may
maintain a parallel snapshot, reducer, or state store.

Storage may load the execution and result records independently, but a common `state_version` / `commit_id` must join
them before an immutable `GraphRunState` projection is exposed. Role configuration remains owned by Role/Config.

## Graph frontier execution

The execution fields in `GraphRunState` are the sole durable truth for frontier settlement, resource ownership, and the active execution token. One atomic
`ClaimGraphExecution` transition installs a token-only lease and, when needed, the initial `ResourceSnapshot`.

Inside the facade, `GraphExecutor.issue_session()` is the only supported session creation path. It consumes the prepared claim linearly and issues a
single-consumer `GraphExecutionSession`; the internal session contract is a non-constructible protocol. Each `next(authoritative_state)` call
requires the exact successor of the preceding reducer command and yields at most one typed node completion with one `SettleGraphNode` command. Concurrent
`next()` calls fail closed before reaching the scheduler, and `aclose()` is idempotent and waits for live tasks to stop.
Cancelling `next()` runs close before propagating cancellation; cancelling that same task again during cleanup cannot interrupt the close.

`SettleGraphNode` atomically records that node's settlement and confirmed result, releases its resources, and advances deterministic resource waiters in one
new `GraphRunState`. Resource requirements only affect which pending nodes the single scheduler may select. Once a caller applies a
settlement and acknowledges the successor state, a newly admitted waiter is submitted immediately even when another typed sibling
completion is already queued; an observed ordinary error instead stops all new activations.

The final node settlement persists a stable `RUNNING + SETTLED` frontier first. Routing is resolved only from that persisted barrier and
then produces a standalone `AdvanceGraphFrontier` or `CompleteGraphFrontier` transition. Session queues and task handles are transient;
they are not a store, retry policy, exactly-once guarantee, or second durable state model.

Execution frames/publications carry values or references plus the matching `state_version` and activation coordinate;
they are not a second source of truth. This document records the stable architectural direction. Authoritative public
contracts will be documented alongside their implementation.
