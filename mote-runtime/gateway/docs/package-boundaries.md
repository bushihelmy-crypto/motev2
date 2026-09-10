# Package boundaries

| Package | Owns | Must not own |
| --- | --- | --- |
| `api` | provider-neutral LLM/media DTOs, terminal results, observations, and error vocabulary | provider JSON, stream event wire formats, or tool execution |
| `internal/model` | model identity and capabilities | endpoint, credentials, routing |
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
accepts the other's request. Embedding and reranking may have adapters inside
Gateway's capability catalog, but need a separately versioned boundary DTO
before they can cross a runtime boundary.

Media capability taxonomy reserves `text`, `image`, `audio`, `music`, and
`video` as separate concepts. A later contract must define how music generation,
understanding, transformation, and audio output relate instead of silently
folding music into a generic audio flag.
