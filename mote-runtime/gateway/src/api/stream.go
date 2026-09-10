package api

// StreamSummary is an aggregate terminal fact.  It intentionally contains no
// stream payloads: callers receive delivery events through the local stream
// handle, while Kernel/Langfuse receive this finalized summary once.
type StreamSummary struct {
	LatencyMS          float64  `json:"latency_ms"`
	Streamed           bool     `json:"streamed"`
	ChunkCount         int64    `json:"chunk_count"`
	InputEventCount    int64    `json:"input_event_count,omitempty"`
	OutputEventCount   int64    `json:"output_event_count,omitempty"`
	TimeToFirstByteMS  *float64 `json:"time_to_first_byte_ms,omitempty"`
	TimeToFirstTokenMS *float64 `json:"time_to_first_token_ms,omitempty"`
}
