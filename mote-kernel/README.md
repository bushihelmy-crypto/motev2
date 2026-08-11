# Mote Kernel

Mote Kernel is a durable, state-machine-driven agent kernel. Graphs control execution; state machines control truth.

The project is in its initial architecture and implementation phase. Its intended public entry point is a single `Role`; graph execution, domain state, persistence, and replaceable services remain internal mechanisms.

## Design principles

- One execution engine for every agent flow.
- Graph state and domain state evolve independently and commit atomically.
- Durable state is committed before the in-memory snapshot advances.
- Concurrent nodes share one immutable input snapshot and must treat it as read-only.
- Concrete model, prompt, tool, storage, and extension capabilities enter through narrow typed ports.
- Optional capabilities are selected when a Role is assembled, not checked repeatedly during execution.

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
