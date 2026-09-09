# Contributing to Mote Gateway

Read `AGENTS.md` and the repository root `AGENTS.md` before changing this
project. Keep changes inside the owning package and preserve the one-way
dependency graph.

Before opening a change:

```bash
make check
make lint
```

If a change alters an observable request, response, stream, receipt, or durable
contract, update the matching conformance schema and deterministic cases in the
same change. Do not add live-provider tests to the default quality gate.
