package api

import "encoding/json"

// ArtifactRef identifies bytes owned by an artifact/content service. The
// reference is opaque; it is not a filesystem path, signed URL, credential,
// or inline media payload.
type ArtifactRef struct {
	ArtifactID     string `json:"artifact_id"`
	Revision       int64  `json:"revision"`
	Representation string `json:"representation"`
	Kind           string `json:"kind"`
	MIMEType       string `json:"mime_type"`
	ContentRef     string `json:"content_ref"`
	Digest         string `json:"digest"`
	Size           int64  `json:"size"`
}

// ContentPart is a provider-neutral LLM message part. Media is always carried
// by an ArtifactRef so the Kernel request remains bounded and replayable.
type ContentPart struct {
	Type     string       `json:"type"`
	Text     string       `json:"text,omitempty"`
	Artifact *ArtifactRef `json:"artifact,omitempty"`
}

// Message is one model conversation message. Tool calls here are complete
// model messages; Gateway never discovers or executes a tool.
type Message struct {
	Role       string        `json:"role"`
	Content    []ContentPart `json:"content"`
	Name       string        `json:"name,omitempty"`
	ToolCallID string        `json:"tool_call_id,omitempty"`
	ToolCalls  []ToolCall    `json:"tool_calls,omitempty"`
}

// ResponseFormat requests a model-native structured response. The schema is
// model-owned JSON and is intentionally not interpreted by the Gateway.
type ResponseFormat struct {
	Type   string          `json:"type"`
	Schema json.RawMessage `json:"schema,omitempty"`
}

// GenerationParameters contains controls shared by LLM and media adapters.
// Provider-specific knobs require a reviewed protocol version instead of an
// unbounded map at this boundary.
type GenerationParameters struct {
	Temperature     *float64 `json:"temperature,omitempty"`
	TopP            *float64 `json:"top_p,omitempty"`
	MaxOutputTokens *int64   `json:"max_output_tokens,omitempty"`
	Stop            []string `json:"stop,omitempty"`
	Seed            *int64   `json:"seed,omitempty"`
}
