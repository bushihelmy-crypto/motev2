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

`Graph.run()` has closed entry points for a new run, a transient continuation, and control-only state recovery. Every completed, aborted, or awaiting-resume result carries the authoritative state and a non-optional opaque continuation. Selective resume actions come from the same `Graph` facade. An optional async commit callback receives each scoped reducer candidate—including every individual node settlement—and execution proceeds only from the exact state it confirms. No concrete store or cross-process value recovery is included.

Passing a state with an active execution lease explicitly confirms that its previous attempt has stopped or been lost; `run()` may then fence and reclaim that lease. This boundary does not arbitrate concurrently live workers or make external port side effects exactly-once.

Public execution failures are caught through the same namespace: `Graph.Error` is the base, with `Graph.ValidationError`, `Graph.SnapshotMismatchError`, `Graph.ExecutionLimitError`, and the value admission/unavailability/publication errors for precise handling.

## Documentation

- [Runnable graph examples](example/graph/README.md) cover the public `Graph` facade end to end: topology, loops, nested scopes, concurrent runs, every resume action, checkpoints, limits, cancellation, partial commit handoff, and versioned deployment.
- [Architecture](docs/architecture.md) owns the current facade, execution/state ownership, and persistence boundaries.
- [Execution/state frontier call chain](docs/execution-state-frontier-call-chain.zh-CN.md) explains the current command, reducer, commit, and frontier flow.

## Design principles

- One execution engine for every agent flow.
- Graph state and domain state evolve independently and commit atomically.
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
clones; symbol and field usage; function complexity and effects; call chains; import cycles; and asynchronous ownership.
`make complexity` enforces zero proven debt without exception inventories. `make complexity-ratchet` prevents every
high-recall metric from growing and requires its ceiling to be lowered after an improvement. `make complexity-report`
prints the candidates behind the metrics. Both gates run from `make check`.

Run all repository checks with:

```bash
pre-commit run --all-files
pyright
pytest --cov=mote_kernel
python -m build
```

## Status

Pre-alpha. Public APIs may change until the first stable release.

## License

Apache License 2.0. See [LICENSE](LICENSE).

中文说明见 [README.zh-CN.md](README.zh-CN.md)。
