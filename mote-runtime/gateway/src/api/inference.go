package api

import "encoding/json"

// LLMInput is the Kernel-owned model request payload. Kernel sends only this
// DTO to Gateway: media generation inputs belong to MediaInput and are sent by
// Execution. Generate inputs use Messages; realtime inputs use the flat
// Instructions/Modalities fields and kind=realtime.
type LLMInput struct {
	Kind           string               `json:"kind"`
	Messages       []Message            `json:"messages,omitempty"`
	SystemPrompt   string               `json:"system_prompt,omitempty"`
	Instructions   string               `json:"instructions,omitempty"`
	Modalities     []Modality           `json:"modalities,omitempty"`
	Tools          []ToolDefinition     `json:"tools,omitempty"`
	ToolChoice     *ToolChoice          `json:"tool_choice,omitempty"`
	ResponseFormat *ResponseFormat      `json:"response_format,omitempty"`
	Parameters     GenerationParameters `json:"parameters,omitempty"`
}

// LLMRequest is the provider-neutral Kernel → Gateway request. The model is
// already selected by Router; service, protocol, and credentials are bound by
// Gateway configuration and never appear here.
type LLMRequest struct {
	Kind          RequestKind  `json:"kind"`
	SchemaVersion int          `json:"schema_version"`
	OperationID   string       `json:"operation_id"`
	ModelID       string       `json:"model_id"`
	Operation     Operation    `json:"operation"`
	Modality      Modality     `json:"modality"`
	Mode          DeliveryMode `json:"mode"`
	Input         LLMInput     `json:"input"`
	Features      []Feature    `json:"features"`
	Trace         TraceContext `json:"trace,omitempty"`
}

// LLMOutput is the finalized model answer consumed by Kernel. Tool calls are
// complete values; no live stream fragment is retained here.
type LLMOutput struct {
	Kind       ResultKind      `json:"kind"`
	Text       string          `json:"text,omitempty"`
	Structured json.RawMessage `json:"structured,omitempty"`
	ToolCalls  []ToolCall      `json:"tool_calls,omitempty"`
}

// LLMTerminal is the closed outcome union for one Kernel model call.
type LLMTerminal struct {
	Outcome Outcome       `json:"outcome"`
	Result  *LLMOutput    `json:"result,omitempty"`
	Error   *GatewayError `json:"error,omitempty"`
}

// LLMResponse is the only terminal value returned to Kernel. Observation and
// receipt are finalized before this value is released.
type LLMResponse struct {
	Kind          ResponseKind     `json:"kind"`
	SchemaVersion int              `json:"schema_version"`
	OperationID   string           `json:"operation_id"`
	Mode          DeliveryMode     `json:"mode"`
	Terminal      LLMTerminal      `json:"terminal"`
	Observation   LLMObservation   `json:"observation"`
	Receipt       ReceiptReference `json:"receipt"`
}
