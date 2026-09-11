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

`mote_kernel.execution.Graph` is the sole public graph composition and execution facade. It is a mutable topology builder until its first successful compilation, through `run()` or the read-only `recovery_child_reads()` projection. Compilation validates and freezes one immutable runtime. The facade never retains a run snapshot, session, or transient output as instance state, so one assembled graph can drive independent runs without becoming a second source of truth. Required ports are validated when an agent flow is assembled. Missing optional ports remove their corresponding nodes when graph definitions are assembled, keeping runtime paths deterministic.

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
  deterministic codec for its immutable values. Persistent records retain full bytes, exact compiled coordinates,
  their birth commit and an optional exact Config cursor. Config capabilities never enter the codec.
- `DurableGraphCommit` maps the existing sealed transition to `GraphPersistenceCommit`: scope, expected revision,
  candidate state, commit key, and complete encoded graph-input/publication writes. Reducer commands stay in execution;
  a backend does not interpret them. The writer must atomically persist and confirm this exact request. Confirmation
  compares complete durable facts and bytes, not arbitrary business-object equality or only a state revision. Before
  crossing the writer boundary, the commit owner captures an independent admitted baseline; both the request after the
  call and the returned receipt must equal it, so in-place mutation cannot redefine the acknowledgement.
- `GraphRecovery` binds one already-read checkpoint to a required `DurableGraphCommit` and exactly resolved Config
  capabilities. Recovery and subsequent writes use that commit's single codec; `run(recovery=...)` rejects a separate
  commit argument. Materialization reuses compiler descriptors, scoped-state validation, typed frame
  admission, lineage, routing and graph-output projection before the usual fence/resume/preflight/family driver.
  Missing or conflicting evidence fails before any recovery commit or node invocation; it is not repaired by rerunning
  a settled producer or selecting the latest available value/configuration.
- Delivered envelopes, nested records and commit receipts are fully re-admitted at their read boundaries. Each value
  has one state-owned `GraphEvidenceCommitment`, generated by the execution commit owner from one domain-separated
  canonical evidence tuple: scope/run and activation coordinate, descriptor identity, birth commit, codec
  identity/version, Config cursor metadata (including absence), and payload bytes; publications additionally bind the
  exact settlement execution provenance. The same commitment appears in the authoritative state ledger and persisted
  record. Reassigning a payload or changing any coordinate cannot be legalized by recapturing only the frame.
  Present frame Config must belong to the owning state's Config definition/version, cannot be newer than that state's
  Config revision, and must match its digest at the same revision. One immutable Config revision cannot resolve twice.
  Legitimately Config-free historical frames stay Config-free: available capabilities and newer state never fill them
  implicitly. Config updates are consumed only by Observe and persist with its settlement; recovery adds no update path.
  This Config snapshot cursor is unrelated to the removed caller-supplied Observe cursor.
- Every lifecycle, including completion, retains `GraphRunState.settled_publications`. Each entry owns its activation
  reference, real settlement commit revision, execution token and optional durable value commitment. Checkpoint
  publications must exactly equal that complete success ledger: neither omitted intermediate outputs nor invented
  publications are accepted. There is no terminal compaction mode or second manifest. Completed child outputs are
  reconstructed bottom-up through the existing projection.
- `GraphCheckpoint.child_runs` carries either `ScopedStateBinding` or explicit `UncreatedGraphRun` read evidence.
  State-owned settled/current nested activations determine the required family members. An omitted child record is
  not proof of nonexistence. Only an authoritative negative read for a current pending child permits the existing
  create-if-absent path; confirmed creation replaces that evidence in the existing family owner. A conflicting durable
  write cannot execute a leaf. The Port remains responsible for one complete, consistent read and authoritative CAS.

A state-only `Graph.run(state=...)` does not load a store or recover missing frames. An opaque continuation remains
process-local and non-serializable. Neither is a replacement for a complete checkpoint read.

## Agent authority and recovery

`agent.py` is the sole external recovery composition root. `Agent` holds frozen capabilities, not runtime state,
continuations, an authority cache or a second scheduler. `AgentStart` creates only a never-created run;
`AgentResume` continues an existing run or replays its terminal business result. Answers retain the exact interrupt
question and typed business values. Results expose outputs, failures, interrupts or an abort, never recovery snapshots.

One invocation follows this chain:

1. Acquire exclusive `ExecutionAuthority` for `AgentRunKey(agent_id, run_id)` through `AuthorityPort`.
2. `PersistencePort.load` returns a complete consistent family or explicit `NeverCreated` evidence. Unavailability,
   tombstones, missing records and identity conflicts cannot become a fresh run.
3. Resolve every Config cursor referenced by the checkpoint, then assemble the Graph. Optional `AgentConfig.initial`
   selects only the exact initial snapshot for a new run. Agent never saves Config or substitutes latest history;
   Observe remains the only update consumer.
4. `Graph.recovery_child_reads` reuses compiled topology and lineage to project any missing pending-child coordinates.
   Only when needed, Agent reads again under the same authority. `GraphCheckpoint.admit_child_reads` requires all
   existing facts to remain identical and exactly the requested negative evidence. Backends do not interpret topology.
5. Call the same `Graph.run()` with one codec-bound `DurableGraphCommit`, project its business result, join execution
   cleanup, then release authority. Partial handoffs leave Agent as their original error, not an alternate continuation.

Root `persistence.py` owns backend-neutral Port contracts; `execution/persistence.py` remains the sole Graph encoding,
checkpoint and exact-receipt owner. Every read, commit and reconciliation carries the same opaque authority.
Adapters atomically enforce authority, scoped absence/revision, complete request identity, state/value writes and the
receipt. Identical key/content is an exact replay; different content is a conflict. Kernel owns no lease clock, lock,
backend selector, transport, database schema or tool-execution ledger. If acquisition cannot return a grant, its Port
must resolve any uncertain acquisition itself; release must not invalidate a successor's grant.

`CommitUnknown` triggers reconciliation of the same immutable request, without re-encoding or rerunning a node.
Only `CommitNotApplied` permits a retry within `max_commit_attempts` (default 3). An unresolved outcome, lost authority,
conflict or non-exact acknowledgement stops advancement. The commit owner classifies failure as `GraphCommitError`
until the Graph boundary unwraps its original cause. Worker fan-in joins every task and preserves that classification,
including failures during construction, handoff, fencing or abort. No ancestor or sibling issues another cleanup write
from stale memory. Ordinary non-commit error priority and caller/node cancellation boundaries remain distinct;
authority release failure never replaces an earlier execution error.

Snapshot and receipt-journal test adapters exercise the same Agent API. The separate-process fault matrix covers
pre-write and post-write exits across linear, sibling, loop, nested-family, Config, interrupt, fencing, and typed
Runtime-result boundaries, always recovering with fresh Agent/Graph/codec objects. These adapters are not concrete
production backends and do not prove database durability, network-partition behavior, or an external authority service;
the exact acceptance evidence and limits live in the
[implementation plan](kernel-persistence-implementation-plan.zh-CN.md). Runtime owns tool execution records and crash
reconciliation. The upper driver owns the next task after ReAct END.

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
