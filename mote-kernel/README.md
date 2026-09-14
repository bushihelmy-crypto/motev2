# Mote Kernel

Mote Kernel is a durable, state-machine-driven agent kernel. Graphs control execution; state machines control truth.

The project is in its initial architecture and implementation phase. `mote_kernel.execution.Graph` is the sole public graph composition and execution facade, and `mote_kernel.Agent` is the sole package-level Agent facade; execution/state primitives remain internal development surfaces. The caller-owned `AgentSession` boundary is defined in `mote_kernel.session`.

```python
from mote_kernel.execution import Graph


async def normalize(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(text=values["raw"].strip().lower())


graph = Graph[str]("example.normalize")
graph.add_node(
    "normalize",
    normalize,
    inputs={"raw": Graph.graph_input("raw", str)},
    outputs={"text": str},
)
graph.set_outputs({"text": Graph.node_output("normalize", "text")})

result = await graph.run(Graph.values(raw="  MOTE  "), run_id="example-run")
assert isinstance(result, Graph.CompletedResult)
assert result.outputs["text"] == "mote"
```

Callable nodes declare named input bindings and exact named output types directly on `add_node()`. Input bindings are
the sole value-source/readiness truth; direct, conditional, and join edges are the sole activation truth. A
`Graph.node_output()` binding never creates an execution edge, so every node-output consumer also needs an incoming
control edge. Graph-input-only and zero-input roots remain automatic entries, while `set_outputs()` is only a result
projection and never activates a node. `Graph.values()` creates immutable concrete frames.

`Graph.node_output()` has two typed overloads. `Graph.node_output("producer", "name")` reads a fixed producer;
`Graph.node_output("name")` reads the one control predecessor that actually activated the consumer. A causal-input
node may also be an explicit START entry: the compiler records a graph-input case for its first activation and routed
predecessor cases for later activations. Join targets still cannot implicitly choose one predecessor value. The
compiler derives every possible predecessor from the topology, requires the requested output with one exact type on
all of them, and admits multiple incoming paths only when it can prove them mutually exclusive. Runtime selection
comes only from the state-owned activation cause and its exact publication (or the compiled START input case)—never
from a latest-value scan. The same admission rules apply to transient frames and backend-independent persistent
evidence; no concrete persistence backend is included.

`Graph.run()` supports new runs, process-local continuations and control-only state recovery. Its owner-internal durable
recovery seam materializes a complete checkpoint into that same execution path; it is not another runner. Every
completed, failed, aborted, or awaiting-resume result carries authoritative state and a non-optional opaque continuation.
Selective resume actions come from the same facade. A commit callback receives each scoped reducer candidate and
complete write set; only confirmed state and values become visible. State-only calls do not load missing values, and
continuations are not serializable. Every continuation, including a partial-commit handoff, retains its original commit
capability: omission or `None` inherits it, and a different object is rejected before execution. Durable recovery uses
its bound commit's single codec for reading and writes; changing capabilities requires an authoritative reread, not an
in-memory downgrade. One state-owned evidence commitment binds every persisted value to its availability coordinate,
descriptor, birth commit, codec, payload, Config cursor (including absence), and publication settlement provenance.
`Agent` wires authoritative loading, exact Config resolution, authority and commit reconciliation into this same seam.
Concrete backend implementations remain outside Kernel; phase status and evidence are recorded in the
[implementation plan](docs/kernel-persistence-implementation-plan.zh-CN.md).

Passing a state with an active execution lease explicitly confirms that its previous attempt has stopped or been lost; `run()` may then fence and reclaim that lease. This boundary does not arbitrate concurrently live workers or make external port side effects exactly-once.

Public execution failures are caught through the same namespace: `Graph.Error` is the base, with `Graph.ValidationError`, `Graph.SnapshotMismatchError`, `Graph.ExecutionLimitError`, and the value admission/unavailability/publication errors for precise handling.

## Durable Agent boundary

`mote_kernel.Agent` is immutable wiring, not a resident state cache or another runner. Supply an `agent_id`, a Graph
assembly callable, one typed frame codec, `PersistencePort` and `AuthorityPort`. Every `Agent.run(request)` acquires
exclusive authority, reads the store, assembles and admits the Graph, runs it, projects a business result, then releases
authority after all execution tasks have joined.

- `AgentStart(run_id, values)` creates only a never-created run; an existing run is a conflict.
- `AgentResume(run_id=None, answers=())` resumes the Agent's durable latest run when `run_id` is omitted; passing a
  string selects that exact historical run (including terminal replay). `AgentAnswer` pairs the exact returned
  interrupt question with typed business values. No state, continuation or per-call commit override is exposed.
- Latest selection is only an `AgentLatestHead` index. The `(agent_id, run_id)` family owns the Graph state, value
  evidence, Config cursors, and Session together; the persistence adapter fences the family read with that head.
- `AgentConfig` optionally supplies the Config store/resolver and an exact initial key. Recovery resolves only the
  historical snapshots referenced by state and frames. The existing Graph Config update command remains owned by
  Observe and persists with its settlement; a node may still put a resolved Config in an explicitly returned complete
  `AgentSession` successor, and the Kernel never infers or merges that field. This Config snapshot cursor is unrelated
  to the removed caller-supplied Observe cursor.
- `AgentSession(hook_state, context, config)` is the caller-owned cross-node/cross-run snapshot. A node that needs to
  update it returns a complete successor with `Graph.success(..., session=...)` (typed nodes may return
  `Graph.SessionActivation(value, session)`). Graph state and the full Session envelope share one atomic commit;
  `AgentResult.session` is the last confirmed snapshot, and a later run reuses it only when the Runtime explicitly
  passes it to `AgentStart`. Session Config is durable only as its existing Config cursor; the Config owner must save
  the referenced immutable snapshot before first use. One Graph invocation/family has one serialized Session owner shared
  by root and child scopes: a no-successor transition selects the owner value when it enters the commit boundary, while
  an explicit complete successor wins in confirmed receipt order. Historical frames retain their own provenance and do
  not overwrite the current owner Session during recovery.
- Unknown Graph commits reconcile the same immutable request. Only proven `NotApplied` outcomes retry, within the
  explicit `max_commit_attempts` budget. Authority loss, conflict, mismatched receipts and unresolved outcomes stop
  execution without stale cleanup writes. Runtime, not Kernel, reconciles tool executions.

[Durable Agent import](example/graph/durable_agent_import.py) reuses the import topology and codec with injected Ports;
it does not choose a database, transport or Container. The next task after ReAct END remains an upper-driver decision.

## Documentation

- [Runnable graph examples](example/graph/README.md) cover the public `Graph` facade end to end: topology, loops, nested scopes, concurrent runs, every resume action, checkpoints, limits, cancellation, partial commit handoff, and versioned deployment.
- [Architecture](docs/architecture.md) owns the current facade, execution/state ownership, and persistence boundaries.
- [Execution/state frontier call chain](docs/execution-state-frontier-call-chain.zh-CN.md) explains the current command, reducer, commit, and frontier flow.
- [Cross-module runtime call coupling review](docs/complexity-cross-module-runtime-call-review.zh-CN.md) explains the additional high-recall metric, review workflow, and limitations.

## Design principles

- One execution engine for every agent flow.
- `GraphRunState` is the single state snapshot; execution position, node results, and business facts commit atomically together.
- Durable state is committed before the in-memory snapshot advances.
- Every node activation receives one immutable, descriptor-checked named input frame.
- Concrete model, prompt, tool, storage, and extension capabilities enter through narrow typed ports.
- Optional capabilities are selected when an agent flow is assembled, not checked repeatedly during execution.

## Development

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pre-commit install
pytest --cov=mote_kernel
```

Run `pre-commit install` and `pre-commit run --all-files` from the monorepo root.

The repository-wide AST gate combines independent high-recall detectors for exact, statement-level, and near-miss
clones; symbol and field usage; function complexity and effects; call chains; resolved runtime calls crossing module
boundaries; import cycles; and asynchronous ownership. Cross-module runtime coupling is a high-recall review signal,
not an automatic claim that the dependency is wrong.
`make complexity` enforces zero proven debt without exception inventories. `make complexity-ratchet` prevents every
high-recall metric from growing and requires its ceiling to be lowered after an improvement. `make complexity-report`
prints the candidates behind the metrics. Both gates run from `make check`.

Run all repository checks with:

```bash
pre-commit run --all-files
make typecheck
pytest --cov=mote_kernel
python -m build
```

## Status

Pre-alpha. Public APIs may change until the first stable release.

## License

Apache License 2.0. See [LICENSE](LICENSE).

中文说明见 [README.zh-CN.md](README.zh-CN.md)。
