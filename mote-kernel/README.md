# Mote Kernel

Mote Kernel is a durable, state-machine-driven agent kernel. Graphs control execution; state machines control truth.

The project is in its initial architecture and implementation phase. `mote_kernel.execution.Graph` is the sole public graph composition and execution facade; execution and state primitives remain internal development surfaces.

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
in-memory downgrade. A persistent frame's single digest covers its codec, payload, and Config cursor, including absence.
The current persistence phase supplies typed commit/recovery contracts;
Agent loading and backend integration remain separate work in the [implementation plan](docs/kernel-persistence-implementation-plan.zh-CN.md).

Passing a state with an active execution lease explicitly confirms that its previous attempt has stopped or been lost; `run()` may then fence and reclaim that lease. This boundary does not arbitrate concurrently live workers or make external port side effects exactly-once.

Public execution failures are caught through the same namespace: `Graph.Error` is the base, with `Graph.ValidationError`, `Graph.SnapshotMismatchError`, `Graph.ExecutionLimitError`, and the value admission/unavailability/publication errors for precise handling.

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
