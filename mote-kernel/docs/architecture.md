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

Storage may use separate physical records, but a scoped `GraphRunState` and its complete value write set share one
atomic commit. No independently updated state or value snapshot is exposed. Config payloads and capability resolution
remain owned by Config; graph state and frame evidence carry exact snapshot references, not serialized capabilities.

Every node in one concurrent frontier receives the same immutable input snapshot. Nodes and ports must treat that snapshot as read-only and return typed outcomes instead of mutating it. Kernel does not clone arbitrary domain DTOs; their owner must define them as immutable values.

`mote_kernel.execution.Graph` is the sole public graph composition and execution facade. It is a mutable topology builder until its first `run()`, when it validates and freezes one immutable compiled runtime. The facade never retains a run snapshot, session, or transient output as instance state, so one assembled graph can drive independent runs without becoming a second source of truth. Required ports are validated when an agent flow is assembled. Missing optional ports remove their corresponding nodes when graph definitions are assembled, keeping runtime paths deterministic.

`Graph.run()` starts from typed values or continues from authoritative state and admitted evidence. Failure, interrupt, skip,
node-result, and Hook changes are all inputs to the same `GraphRunCommand` path; resume is not a second runner. A fresh
or control-only state run without a commit callback applies pure state transitions process-locally. A continuation
instead inherits its original commit capability. With a callback, execution offers every command,
candidate, and complete typed write set for one atomic commit, and advances only when the callback confirms the exact
candidate. The family driver prepares the immutable frame projection before I/O and installs it only after confirmation.
This boundary does not make an in-memory callback durable.

`execution/graph_result.py` owns sealed family results, partial handoffs, and continuation provenance. Its immutable
`ContinuationSnapshot` carries the exact `GraphCommit` capability through every handoff; it does not import or detect
a concrete persistence implementation. An omitted commit or `None` inherits that capability. An explicit commit must
be the same object, not merely compare equal; transient continuations also cannot acquire a commit halfway through a
run. Rebinding requires a new authoritative checkpoint read. The frame/evidence owner remains `run_context.py`, and
task outcomes and execution dispositions remain in `result.py`; there is no import cycle or untyped capability token.

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

Each root or child run has its own scoped revision and `GraphCommitKey`; a family read must be consistent without
pretending that all child revisions are equal. `GraphCheckpoint` is a read envelope around these existing states and
their value evidence, not another runtime state.

## Backend-independent persistence

`execution/persistence.py` owns encoded value evidence, the durable commit adapter, and checkpoint materialization.
These are owner-internal contracts; `Graph` remains the only execution facade.

- `graph/codec.py` owns `FrameCodec`, shared by resume input and persistent frames. A domain supplies a versioned,
  deterministic codec for its immutable values. Persistent frames retain full bytes, their integrity digest, exact
  compiled coordinates, and an optional exact Config cursor. Config capabilities never enter the codec.
- `DurableGraphCommit` maps the existing sealed transition to `GraphPersistenceCommit`: scope, expected revision,
  candidate state, commit key, and complete encoded graph-input/publication writes. Reducer commands stay in execution;
  a backend does not interpret them. The writer must atomically persist and confirm this exact request. Confirmation
  compares complete durable facts and bytes, not arbitrary business-object equality or only a state revision.
- `GraphRecovery` binds one already-read checkpoint to a required `DurableGraphCommit` and exactly resolved Config
  capabilities. Recovery and subsequent writes use that commit's single codec; `run(recovery=...)` rejects a separate
  commit argument. Materialization reuses compiler descriptors, scoped-state validation, typed frame
  admission, lineage, routing and graph-output projection before the usual fence/resume/preflight/family driver.
  Missing or conflicting evidence fails before any recovery commit or node invocation; it is not repaired by rerunning
  a settled producer or selecting the latest available value/configuration.
- Delivered envelopes and commit receipts are re-admitted at their read boundaries. `EncodedFrame.frame_digest` is the
  single, domain-separated digest of canonical codec identity/version and Config cursor metadata plus payload bytes.
  Cursor absence is explicit metadata, not an integrity opt-out. Removal, insertion, or replacement invalidates an
  existing digest before decoding, node calls, or writes; payload-only digests are not another accepted format.
  Present frame Config must belong to the owning state's Config definition/version, cannot be newer than that state's
  Config revision, and must match its digest at the same revision. One immutable Config revision cannot resolve twice.
  Legitimately Config-free historical frames stay Config-free: available capabilities and newer state never fill them
  implicitly. Config updates are consumed only by Observe and persist with its settlement; recovery adds no update path.
- Every lifecycle, including completion, retains the existing `GraphRunState.settled_activations` ledger. Publications
  must exactly equal that complete success set: neither omitted intermediate outputs nor invented publications are
  accepted. There is no terminal compaction mode or second manifest. Completed child outputs are reconstructed
  bottom-up through the existing projection.
- `GraphCheckpoint.child_runs` carries either `ScopedStateBinding` or explicit `UncreatedGraphRun` read evidence.
  State-owned settled/current nested activations determine the required family members. An omitted child record is
  not proof of nonexistence. Only an authoritative negative read for a current pending child permits the existing
  create-if-absent path; confirmed creation replaces that evidence in the existing family owner. A conflicting durable
  write cannot execute a leaf. The Port remains responsible for one complete, consistent read and authoritative CAS.

A state-only `Graph.run(state=...)` does not load a store or recover missing frames. An opaque continuation remains
process-local and non-serializable. Neither is a replacement for a complete checkpoint read.

P1 implements this execution seam, not an Agent loader or a concrete backend. External load, exact Config resolution,
execution authority and uncertain **persistence commit** reconciliation are reserved for `agent.py` in P2; see the
[implementation plan](kernel-persistence-implementation-plan.zh-CN.md). Tool execution records and crash reconciliation
belong to Runtime. Receiving a new task after ReAct END belongs to the upper driver, not this persistence path.

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

Execution publications carry the acknowledged scoped revision and execution provenance with their exact descriptor
and activation coordinate. They are immutable evidence from the same commit, not independently mutable state.
