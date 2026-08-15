# Mote Kernel

Mote Kernel is a durable, state-machine-driven agent kernel. Graphs control execution; state machines control truth.

The project is in its initial architecture and implementation phase. `mote_kernel.execution.Graph` is the sole public graph composition and execution facade; execution and state primitives remain internal development surfaces.

```python
from mote_kernel.execution import Graph


async def normalize(value: str) -> str:
    return value.strip().lower()


graph = Graph[str, str]("example.normalize")
graph.add_node("normalize", normalize)
graph.add_edge(Graph.START, "normalize")
graph.add_edge("normalize", Graph.END)

result = await graph.run("  MOTE  ", run_id="example-run")
assert result.completed
assert result.outputs[0].output == "mote"
```

`Graph.run()` also accepts the authoritative state returned by a prior invocation and selective resume actions created by the same facade. An optional async commit callback receives every reducer candidate—including every individual node settlement—and execution continues only from the exact state it confirms. No concrete store is included.

Passing a state with an active execution lease explicitly confirms that its previous attempt has stopped or been lost; `run()` may then fence and reclaim that lease. This boundary does not arbitrate concurrently live workers or make external port side effects exactly-once.

Public execution failures are caught through the same namespace: `Graph.Error` is the base, with `Graph.ValidationError`, `Graph.SnapshotMismatchError`, and `Graph.ExecutionLimitError` for precise handling.

## Design principles

- One execution engine for every agent flow.
- Graph state and domain state evolve independently and commit atomically.
- Durable state is committed before the in-memory snapshot advances.
- Concurrent nodes share one immutable input snapshot and must treat it as read-only.
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
