# Mote Gateway

`gateway` is the Go implementation boundary for outbound model access. It
exposes two caller-owned invocation profiles: Kernel `LLMRequest`/
`LLMResponse` for language and realtime calls, and Execution
`MediaRequest`/`MediaResponse` for image, audio, music, video, and
transcription calls. Embeddings and reranking remain reserved for a separately
versioned profile. It is not a model router and it is not an MCP or
tool-execution runtime.

The repository is currently at the project-scaffold stage. The package layout
and quality gates are intentional; protocol and service implementations are
added behind these boundaries as their contracts are accepted.

## Module layout

The Go module is `src/` so imports do not expose an extra implementation
directory segment:

```text
gateway/
├── config/                      # non-authoritative local examples only
├── docs/                        # package and development decisions
├── scripts/                     # deterministic repository checks
├── src/                         # Go module root
│   ├── api/                     # provider-neutral public DTO boundary
│   ├── cmd/gateway/             # process entry point
│   ├── internal/                # composition and domain implementation
│   │   ├── application/         # invocation orchestration
│   │   ├── admission/           # compatibility gate
│   │   ├── plan/                # immutable call plan
│   │   ├── model/               # model facts/catalog
│   │   ├── protocol/            # adapter contracts/registry
│   │   ├── service/             # connector contracts/registry
│   │   ├── cache/               # prompt-cache and result-cache policy
│   │   ├── usage/               # per-call usage accumulation
│   │   ├── receipt/             # receipt construction
│   │   ├── telemetry/           # bounded-cardinality instruments
│   │   └── upstream/            # internal wire boundary
│   ├── protocols/               # concrete protocol adapters
│   ├── connectors/              # concrete service connectors
│   ├── upstream/                # HTTP/SSE/WebSocket/EventStream/pool mechanics
│   └── ports/                   # narrow external dependencies
└── Makefile                     # project commands delegate to src/
```

`api/` is the only place for provider-neutral request/result/observation
shapes. `LLMInput` and `MediaInput` are intentionally different DTOs; a
protocol or durable cross-language shape is not considered released merely
because a Go type exists; it must first have an accepted conformance contract
under the repository's `conformance/` owner.

## Local commands

Run commands from `gateway/`:

```bash
make check              # complete quality gate
make test-unit          # unit tests (race detector enabled)
make test-integration   # deterministic integration harness
make architecture       # package and dependency ownership checks
make complexity         # exact complexity ratchet
make lint               # golangci-lint with the checked-in policy
make build              # bin/gateway
make docker-build       # build the reproducible scaffold image
```

`make check` also runs the deterministic integration harness, architecture and
complexity ratchets, module hygiene, vulnerability/secret scanning, and legal
metadata checks. `make tools` installs the pinned quality tools into the ignored
`.tools/bin` directory.

The module can also be used directly:

```bash
go -C src test ./...
go -C src vet ./...
go -C src build ./...
```

There are no third-party dependencies in the scaffold. Add a dependency only
when a concrete owner and consumer need it, then commit the resulting module
metadata and update the relevant quality gate.

The Go baseline is Go 1.26.6 (`src/go.mod`, `toolchain` directive, and
`.go-version`). CI tests the baseline and Go 1.27.1; see
[`quality/toolchain.md`](quality/toolchain.md).

## Boundary rules

- Router chooses a model; Gateway receives that selection and never chooses a
  fallback model, service, or protocol.
- Kernel Think uses only `LLMInvocation`; Execution uses `MediaInvocation` for
  media generation/understanding. The two request and response DTO pairs are
  not interchangeable.
- Protocol adapters encode/decode wire payloads and stream events.
- Service connectors resolve targets and credentials and perform cloud signing.
- `upstream/` owns outbound network mechanics and connection reuse.
- Model-native tool calling (definitions, choices, complete calls, and results)
  is part of the inference contract. A live stream may deliver intermediate
  fragments to its caller, but the Gateway → Kernel DTO contains only the
  finalized call/result. Tool discovery and execution are outside this project;
  no MCP package belongs here.
- Prompt/context-cache accounting is mandatory gateway behavior. Whole-result
  caching is opt-in and exact-match only until a separate contract says more.
- Receipts use external persistence ports; this project does not own a database.

Secrets must remain secret references at the boundary and must never enter
logs, traces, cache keys, receipts, test vectors, or build artifacts.

## Release shape

`.goreleaser.yaml` builds the `cmd/gateway` artifact from the `src` module. The
version is injected from the release tag through `internal/buildinfo`; there is
no second checked-in version constant to drift.
