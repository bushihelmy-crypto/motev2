# Gateway model invocation contract

This document defines `gateway_invocation` version `1`, the provider-neutral
model boundary used by the runtimes. The JSON Schema in
[`schemas/protocol/gateway_invocation.v1.schema.json`](../schemas/protocol/gateway_invocation.v1.schema.json)
is authoritative. Go types under `mote-runtime/gateway/src/api` and any
future Python or Rust adapter are projections of that schema, not additional
protocols.

Version 1 has two caller-owned profiles. They deliberately use different DTOs:

* `kernel_llm`: Kernel sends language-model requests (`LLMRequest`) and
  receives `LLMResponse`.
* `execution_media`: Execution sends media generation/understanding requests
  (`MediaRequest`) and receives `MediaResponse`.

The profile is selected by the conformance case envelope. It is not a field
that callers may use to reinterpret a request, and a request's `kind` remains
the wire discriminator.

## Scope

Gateway owns outbound model access only. The contract does not contain model
routing, provider selection, MCP/discovery/execution, agent flow, billing
policy, or network configuration. Connection reuse and protocol framing are
implementation concerns under Gateway's upstream packages.

The request has these fixed dimensions:

| Field | Meaning |
| --- | --- |
| `operation_id` | Stable identity of one logical model operation; it is not a provider request id. |
| `model_id` | Model already selected by Router/Kernel. Gateway never replaces it. |
| `operation` | Semantic model operation. |
| `modality` | Primary input/output modality. `music` is distinct from `audio`. |
| `mode` | A profile-allowed lifecycle: `unary`, `server_stream`, `duplex`, or `async`. |
| `input` | Operation-discriminated canonical model input. |
| `features` | Required capabilities such as native tool calls, structured output, prompt cache, and usage. |
| `trace` | Optional explicit correlation identities supplied by the caller. |

The operation/modality/input combinations fixed by v1 are:

| Profile | Operation | Modality | Mode | Input kind |
| --- | --- | --- | --- | --- |
| `kernel_llm` | `generate` | `text` | `unary`, `server_stream` | `generate` |
| `kernel_llm` | `realtime` | `audio` | `duplex` | `realtime` |
| `execution_media` | `image_generation` | `image` | `unary`, `server_stream`, `async` | `image_generation` |
| `execution_media` | `audio_generation` | `audio` | `unary`, `server_stream`, `async` | `audio_generation` |
| `execution_media` | `music_generation` | `music` | `unary`, `server_stream`, `async` | `music_generation` |
| `execution_media` | `video_generation` | `video` | `unary`, `server_stream`, `async` | `video_generation` |
| `execution_media` | `audio_transcription` | `audio` | `unary`, `server_stream`, `async` | `audio_transcription` |

Embedding and reranking are Gateway capabilities reserved for a separately
versioned profile and DTO. They MUST NOT be represented as a `kernel_llm` or
`execution_media` request by compatibility shims. Service kind, endpoint,
credentials, provider protocol, retry/fallback policy, and connection-pool
settings are intentionally absent from the request; they are Gateway
composition and outbound transport concerns.

### Profile DTOs

`kernel_llm` is the only boundary used by Kernel Think. Its `LLMInput` carries
conversation messages, system/instruction text, model-native tool definitions
and choices, structured-output preferences, and generation parameters. A
realtime input uses `kind=realtime` and is opened as a duplex session; it does
not turn media generation into an LLM operation.

`execution_media` is the only boundary used by Execution for media work. Its
`MediaInput` is operation-specific: speech synthesis uses `text` and `voice`,
image/music/video generation uses `prompt`, and transcription uses an opaque
audio `media` artifact. Media results are artifact references or a bounded
transcript, never inline bytes. These fields are intentionally not aliases of
`LLMInput` and must not be merged into a generic request map.

## Caller-specific terminal response

Every invocation that reaches the Gateway boundary has exactly one terminal
response. The profile determines its nominal type; there is no catch-all
response DTO:

```text
Kernel Think  -> LLMInvocation   -> LLMRequest   -> LLMResponse
Execution     -> MediaInvocation -> MediaRequest -> MediaResponse
```

Both response types contain the same terminal/observation primitives, but the
request and result unions stay separate. In particular, a `MediaRequest` is
not assignable to the Kernel LLM invocation and a `LLMRequest` is not accepted
by the Execution media invocation.

The common terminal envelope contains:

```text
LLMResponse | MediaResponse
├── schema_version / operation_id / mode
├── terminal
│   ├── outcome = succeeded   → result
│   ├── outcome = submitted  → task handle
│   └── outcome = failed | cancelled | unknown → safe error
├── observation
│   ├── explicit trace/session/generation correlation
│   ├── requested/resolved model and opaque resource identities
│   ├── canonical input snapshot
│   ├── final finish disposition
│   ├── final usage and decimal cost
│   ├── aggregate timing and stream counters
│   ├── prompt/result cache observations
│   └── terminal summaries of physical attempts
└── receipt
```

`terminal.result` is the sole canonical output projection.  The observation
sidecar deliberately has no second output field: Kernel's observability
adapter uses the terminal result as Langfuse generation output and uses the
sidecar for model, usage, cost, timing, cache, and correlation metadata.
This prevents two competing representations of the model answer.

The sidecar is backend-neutral.  It must not contain a `langfuse` field, an
SDK object, logger object, provider secret, signed header, raw provider body,
or a cache key.  Endpoint, tenant, credential-slot, and cache-key values are
opaque fingerprints/identities only.

## Streaming and realtime

Streaming is a delivery concern, not a second result protocol.

* A server-stream implementation may decode upstream chunks and deliver them
  to its caller while the call is active.
* A duplex implementation may interleave caller sends and model receives.
* The implementation accumulates/normalizes the complete model result and
  calls exactly one finalization path.
* After finalization, it returns one profile response (`LLMResponse` or
  `MediaResponse`) with the complete
  result and aggregate timing (`streamed`, `chunk_count`, TT* values where
  available).

Intermediate text fragments, media fragments, reasoning fragments, tool-call
argument fragments, and transport events MUST NOT be placed in the terminal
response or its observation sidecar.  They are neither Langfuse generation
output nor durable Gateway log facts.  A caller that needs live display owns
the local delivery stream; the Kernel boundary remains final-only.

`chunk_count` and timing counters are aggregate facts.  They do not permit a
consumer to reconstruct or treat a partial stream as a completed result.  A
cancelled or unknown call carries its terminal error and whatever aggregate
facts are known; it must not claim success or create a result-cache entry.

## Native model tool calls

Tool definitions and choices are ordinary model input.  A successful generate
result may contain complete `tool_calls` with stable call ids, names, and
final arguments (plus an optional exact raw argument string when a caller
needs it).  A tool call is model output, not authorization to run anything.

This contract has no MCP discovery, tool registry, executor, approval flow,
agent loop, or tool fallback callback.  Kernel/Act/another owner decides what
to do with a completed model call.

## Observability projection

Kernel already owns the Langfuse integration boundary.  Its adapter should
project one terminal response as follows:

| Kernel/Langfuse value | Gateway source |
| --- | --- |
| generation/operation id | `operation_id`, `observation.correlation.generation_id`, and `model_call_id` |
| trace/session/parent | `observation.correlation` |
| generation model/provider | `observation.model` |
| generation input | `observation.input` (the canonical request input) |
| generation output | `terminal.result` when `outcome=succeeded` |
| usage | `observation.usage` |
| cost | `observation.cost` |
| latency/TTFT/chunk facts | `observation.timing` |
| finish reason | `observation.finish` |
| error level/message | `terminal.error` and failed attempt summaries |
| retry/physical-attempt context | `observation.attempts` |
| cache metrics | `observation.cache` |
| durable operation identity | `receipt` |

Absent usage/cost values are represented by an availability state, not by a
fabricated zero.  Token dimensions are optional so a provider that reports no
usage remains distinguishable from a provider that reports zero.  Cost uses a
decimal string to avoid binary floating-point billing drift.

## Terminal outcomes and recovery

* `succeeded`: a normalized result is available and the receipt is durable.
* `submitted`: an asynchronous task was accepted; only a durable task handle
  is returned.  Polling, cancellation, and reconciliation are separate
  operations.
* `failed`: the call reached a known failure state with a stable error code.
* `cancelled`: caller, deadline, or upstream cancellation ended the call.
* `unknown`: the caller cannot prove whether the upstream operation settled;
  reconciliation is required and the result is not cacheable.

For an operation that has started, a typed terminal response is preferred to a
bare transport error so Kernel can preserve the observation and receipt.  A
Go `error` remains appropriate when admission, serialization, or transport
failure prevents Gateway from forming any terminal envelope.

## Cache and network boundaries

Gateway owns prompt/context-cache semantics, result-cache policy, and their
normalized final observations.  Storage, connection pools, token caches,
HTTP/SSE/WebSocket framing, TLS, deadlines, and backpressure remain behind
their own ports/owners.  `observation.cache` reports facts after accounting is
finalized; it is not a second cache API or a telemetry backend.

## Lifecycle rules

The local Go interfaces preserve these rules:

* unary invokes once and returns once;
* server-stream opens one caller-owned stream, has one finalization path and
  one idempotent close path;
* duplex has one session close path and independent send/receive cancellation;
* async submits once and never hides an unbounded polling loop.

The method names are not wire fields.  Cross-language tests validate the
request, terminal envelope, and final observation from this specification.
Released vectors are immutable; a semantic change requires a new case or a
new protocol version.
