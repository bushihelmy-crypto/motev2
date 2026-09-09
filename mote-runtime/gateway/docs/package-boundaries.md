# Package boundaries

| Package | Owns | Must not own |
| --- | --- | --- |
| `api` | provider-neutral inference DTOs and event vocabulary | provider JSON or tool execution |
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
agent loops, and model routing are intentionally absent.

Media capability taxonomy reserves `text`, `image`, `audio`, `music`, and
`video` as separate concepts. A later contract must define how music generation,
understanding, transformation, and audio output relate instead of silently
folding music into a generic audio flag.
