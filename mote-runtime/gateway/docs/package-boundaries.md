# Package boundaries

| Package | Owns | Must not own |
| --- | --- | --- |
| `api` | service- and protocol-neutral LLM/media DTOs, terminal results, observations, and error vocabulary | upstream protocol JSON, stream event wire formats, or tool execution |
| `internal/model` | immutable model definitions, operation capabilities (including reasoning modes/effort), model defaults, complete source conversion, and atomic Catalog refresh | endpoint, credentials, service/protocol selection, routing, or pricing |
| `internal/protocol` | adapter contracts, protocol-owned facts, and concrete protocol adapters | cloud signing or host resolution |
| `internal/service` | service config, exact-model deployability, service-owned facts, target resolution, authorization, and concrete connectors | protocol payload encoding or model routing |
| `internal/admission` | deterministic compatibility gate | fallback or network calls |
| `internal/cache` | prompt-cache semantics and explicit result-cache policy | connection/token pools |
| `internal/upstream` | outbound network mechanics and concrete transports | model semantics or inbound RPC |
| `ports` | narrow persistence/secret/clock dependencies | implementations and databases |
| `receipt` / `telemetry` | audit metadata and bounded metrics | billing, user governance, UI |

`internal` does not mean "unusable by a sibling package". It only prevents
imports from outside this Gateway module. Concrete implementations live below
their owner and depend on that owner's contract in one direction:

```text
internal/protocol/* -> internal/protocol, internal/upstream
internal/service/*  -> internal/service, internal/upstream
internal/upstream/* -> internal/upstream
```

The contract packages never import those concrete implementations, and a
protocol, connector, or transport must not jump across to another owner's
implementation or admission/model internals. Connector call views carry the
admission-owned identity and normalized request; they do not recreate a plan.

Model-native tool calls are part of `api`; MCP, tool discovery, tool execution,
agent loops, and model routing are intentionally absent. Streaming fragments
are caller-owned delivery values and never become a second Kernel/Langfuse
result or log protocol.

`internal/model` does not define a behavioral `BaseLLM`. Its shared shape is
`model.Config`; per-model differences are declared as operation capability
data. Production records arrive through `ports.ModelCatalogSource` and are
converted as one complete batch. Production has no embedded seed, override, or
fallback data source; the checked-in gzip file is compiled only into model
tests. The resulting `model.Definition` is immutable and uses exact
`BaseModel` lookup, never aliasing, routing, discovery, or fallback.

For service- and protocol-neutral generation and reasoning controls already known to `api`,
parameter resolution applies model defaults, then explicit request values, and
removes controls the selected model does not support. It does not retain a
second list of removed controls. Supported numeric values are clamped to the
model's admitted minimum and maximum. Invalid bounds and out-of-range defaults
reject catalog construction instead of creating a runtime correction path.
Unknown DTO fields still fail closed, and required semantic features such as
tool calling or structured output are rejected by admission when unsupported;
they are not silently removed.

Admission uses one requirements reducer over the complete typed input. System
prompts, realtime instructions, message parts, media sources, and transcription
artifacts all contribute their real input modality before the model × protocol
× service intersection. Artifact `kind` is the sole kind-to-modality owner;
Gateway never guesses from MIME type or provider labels. Explicit features are
unioned with structurally implied features, so neither representation can
bypass capability admission.

Reasoning is an LLM-only model capability. It declares the supported
`disabled`, `enabled`, and `adaptive` thinking modes and, per mode, the
supported effort levels. `adaptive` plus an effort is valid only when the
selected model declares that pair; `disabled` never carries an effort. The
public request carries this as `LLMInput.Reasoning`; provider names such as
`budget_tokens`, `thinkingBudget`, and `reasoning_effort` stay in adapters.

All public delivery entries use one execution path: the inbound invocation
owner strictly decodes/schema-validates bytes and produces a typed frame with
field presence, then Gateway performs deterministic admission, creates one
immutable `admission.AdmittedLLM` or `admission.AdmittedMedia`, and dispatches
that value to the adapter. Gateway exposes no raw-request convenience entry
that could guess field presence or bypass the authoritative schema, and it
never parses inbound JSON. The composition root supplies one
`InvocationConfig` containing the immutable catalog and the already-selected
protocol and service descriptors; Gateway has no permissive production
descriptor and does not choose either dimension. Concrete inbound and outbound
adapters remain outside this unreleased scaffold until their owners wire them
in production.

Embedding capability is model data, not a generation subclass. It declares
accepted input modalities and embedding-only output. A fixed vector width
filters a caller-supplied `dimensions`; an adjustable width owns one default
and its known bounds, and clamps the Kernel value to those bounds. Catalog
records contain only model-owned facts. They have no service, protocol,
endpoint, credential, pricing, or family metadata. A custom model enters
through the same complete `ModelCatalogSource` record path as every other
model.

The cross-language invocation contract is authoritative in the repository root
at `conformance/`. In particular, the current `gateway_invocation` contract defines the
operation/modality/mode vocabulary and stream/session terminal semantics. The
Go entries in `src/invocation.go` only adapt that contract to typed Go
capabilities; they must not grow a private wire schema or lifecycle variant.

The caller split is fixed for the current invocation contract:

```text
Kernel Think -> LLMRequestFrame   -> gateway.InvokeLLM / OpenLLMStream / OpenDuplex
Execution    -> MediaRequestFrame -> gateway.InvokeMedia / OpenMediaStream / SubmitMedia
```

`MediaInput` is not an alias of `LLMInput`, and no delivery entry accepts the
other profile's frame. Embedding is implemented in Gateway's model
catalog, while embedding invocation and reranking still need separately
versioned boundary DTOs before they can cross a runtime boundary.

Media capability taxonomy reserves `text`, `image`, `audio`, `music`, and
`video` as separate concepts. A later contract must define how music generation,
understanding, transformation, and audio output relate instead of silently
folding music into a generic audio flag.
