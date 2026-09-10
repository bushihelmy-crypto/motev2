package api

import "encoding/json"

// ToolDefinition is a model-native function schema supplied by the caller.
// It is not a registration record and carries no executor or permission.
type ToolDefinition struct {
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	InputSchema json.RawMessage `json:"input_schema"`
}

// ToolChoice controls whether the model may emit a native tool call.
type ToolChoice struct {
	Mode string `json:"mode"`
	Name string `json:"name,omitempty"`
}

// ToolCall is the complete, finalized model-native call returned in the
// terminal result.  Streaming argument fragments are an internal delivery
// concern and are never part of the Kernel/Langfuse response DTO.
type ToolCall struct {
	CallID       string          `json:"call_id"`
	Name         string          `json:"name"`
	Arguments    json.RawMessage `json:"arguments"`
	ArgumentsRaw string          `json:"arguments_raw,omitempty"`
}
