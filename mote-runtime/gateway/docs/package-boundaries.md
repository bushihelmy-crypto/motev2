# Package boundaries

| Package | Owns | Must not own |
| --- | --- | --- |
| `api` | service- and protocol-neutral LLM/media DTOs, terminal results, observations, and error vocabulary | upstream protocol JSON, stream event wire formats, or tool execution |
| `internal/model` | immutable model definitions, operation capabilities, model defaults, and validated Kernel overrides | endpoint, credentials, service/protocol selection, routing, or pricing |
| `internal/protocol` | adapter contracts and wire feature declarations | cloud signing or host resolution |
| `internal/service` | service config, target resolution, authorization | protocol payload encoding |
| `internal/admission` | deterministic compatibility gate | fallback or network calls |
| `internal/plan` | immutable model/protocol/service combination | re-selection during a call |
| `internal/cache` | prompt-cache semantics and explicit result-cache policy | connection/token pools |
| `internal/upstream` / `upstream` | outbound network mechanics | model semantics or inbound RPC |
| `ports` | narrow persistence/secret/clock dependencies | implementations and databases |
| `receipt` / `telemetry` | audit metadata and bounded metrics | billing, user governance, UI |

Model-native tool calls are part of `api`; MCP, tool discovery, tool execution,
agent loops, and model routing are intentionally absent. Streaming fragments
are caller-owned delivery values and never become a second Kernel/Langfuse
result or log protocol.

`internal/model` does not define a behavioral `BaseLLM`. Its shared shape is
`model.Config`; per-model differences are declared as operation capability
data. Catalog defaults are combined once with model overrides carried from
Kernel by the Gateway invocation adapter. The resulting `model.Definition` is
immutable and uses exact model-ID lookup, never aliasing, routing, discovery,
or fallback.

For service- and protocol-neutral generation controls already known to `api`,
parameter resolution applies model defaults, then explicit request values, and
removes controls the selected model does not support. It does not retain a
second list of removed controls. Supported numeric values are clamped to the
model's admitted minimum and maximum. Invalid bounds and out-of-range defaults
reject catalog construction instead of creating a runtime correction path.
Unknown DTO fields still fail closed, and required semantic features such as
tool calling or structured output are rejected by admission when unsupported;
they are not silently removed.

Embedding capability is model data, not a generation subclass. It declares
accepted input modalities and embedding-only output. A fixed vector width
filters a caller-supplied `dimensions`; an adjustable width owns one default
and its known bounds, and clamps the Kernel value to those bounds. The built-in
catalog is generated from pinned new-api and Bifrost inputs without importing
service, protocol, endpoint, credential, pricing, or family metadata. Kernel
uses the same validated override path to replace a built-in definition or add
a complete custom model.

The cross-language invocation contract is authoritative in the repository root
at `conformance/`. In particular, `gateway_invocation` v1 defines the stable
operation/modality/mode vocabulary and stream/session terminal semantics. The
Go interfaces in `src/invocation.go` only adapt that contract to typed Go
capabilities; they must not grow a private wire schema or lifecycle variant.

The caller split is fixed for v1:

```text
Kernel Think  -> LLMInvocation   -> api.LLMRequest   -> api.LLMResponse
Execution     -> MediaInvocation -> api.MediaRequest -> api.MediaResponse
```

`MediaInput` is not an alias of `LLMInput`, and neither invocation interface
accepts the other's request. Embedding is implemented in Gateway's model
catalog, while embedding invocation and reranking still need separately
versioned boundary DTOs before they can cross a runtime boundary.

Media capability taxonomy reserves `text`, `image`, `audio`, `music`, and
`video` as separate concepts. A later contract must define how music generation,
understanding, transformation, and audio output relate instead of silently
folding music into a generic audio flag.
