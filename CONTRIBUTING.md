# Contributing

Thank you for helping build Mote v2.

Changes should be scoped to the canonical owner of the behavior. Cross-language contract changes must include matching conformance schema or case changes and runner updates.

## Local checks

Install the development dependencies for every affected child project. For the Python Kernel:

```bash
cd mote-kernel
python -m pip install -e '.[dev]'
make check
```

For the Rust Infra:

```bash
cd mote-infra/local
make check
```

For the Cloudflare Python Infra:

```bash
cd mote-infra/cloudflare/python
uv sync --locked
pnpm install --frozen-lockfile
make check
```

For the Cloudflare TypeScript Infra:

```bash
cd mote-infra/cloudflare/ts
pnpm install --frozen-lockfile
pnpm run check
```

Install `cargo-deny` before running the dependency license, source, and advisory checks:

```bash
cargo install cargo-deny --version 0.19.7 --locked
cd mote-infra/local
make security
```

Run repository hooks from the monorepo root:

```bash
pre-commit install
pre-commit run --all-files
```

Pull requests must identify the canonical owner, state and lifecycle impact, persistence and recovery behavior, public contract impact, and verification performed.

Do not commit generated reports, build artifacts, local state, credentials, or nested repositories. By contributing, you agree that contributions are licensed under the license of the affected project.
