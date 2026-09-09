package api

// MediaInput is deliberately separate from LLMInput. Execution owns media
// generation/understanding requests; Kernel does not construct this DTO.
type MediaInput struct {
	// Kind is the operation-specific input discriminator. It is checked by the
	// media admission owner together with MediaRequest.Operation.
	Kind string `json:"kind"`

	// Text is used by speech synthesis; Prompt is used by image/music/video
	// generation. Source/Media are opaque input artifacts for transform and
	// transcription operations.
	Prompt         string               `json:"prompt,omitempty"`
	Text           string               `json:"text,omitempty"`
	Voice          string               `json:"voice,omitempty"`
	Source         *ArtifactRef         `json:"source,omitempty"`
	Media          *ArtifactRef         `json:"media,omitempty"`
	Language       string               `json:"language,omitempty"`
	ResponseFormat string               `json:"response_format,omitempty"`
	Speed          *float64             `json:"speed,omitempty"`
	Count          *int64               `json:"count,omitempty"`
	DurationMS     *int64               `json:"duration_ms,omitempty"`
	Width          *int64               `json:"width,omitempty"`
	Height         *int64               `json:"height,omitempty"`
	FPS            *int64               `json:"fps,omitempty"`
	Size           string               `json:"size,omitempty"`
	Quality        string               `json:"quality,omitempty"`
	Parameters     GenerationParameters `json:"parameters,omitempty"`
}

// MediaRequest is the provider-neutral Execution → Gateway request for a
// media operation. It is not an alias of LLMRequest.
type MediaRequest struct {
	Kind          RequestKind  `json:"kind"`
	SchemaVersion int          `json:"schema_version"`
	OperationID   string       `json:"operation_id"`
	ModelID       string       `json:"model_id"`
	Operation     Operation    `json:"operation"`
	Modality      Modality     `json:"modality"`
	Mode          DeliveryMode `json:"mode"`
	Input         MediaInput   `json:"input"`
	Features      []Feature    `json:"features"`
	Trace         TraceContext `json:"trace,omitempty"`
}

// MediaOutput is a finalized media result. Bytes are represented by artifact
// references; Gateway does not put large media in a Kernel/domain payload.
type MediaOutput struct {
	Kind       ResultKind          `json:"kind"`
	Artifacts  []ArtifactRef       `json:"artifacts,omitempty"`
	Transcript string              `json:"transcript,omitempty"`
	Segments   []TranscriptSegment `json:"segments,omitempty"`
}

// TranscriptSegment is an optional bounded transcript annotation. It carries
// no provider-specific confidence or raw response fields.
type TranscriptSegment struct {
	StartMS int64  `json:"start_ms"`
	EndMS   int64  `json:"end_ms"`
	Text    string `json:"text"`
}

// MediaTerminal is the closed outcome union for one Execution media call.
type MediaTerminal struct {
	Outcome Outcome       `json:"outcome"`
	Result  *MediaOutput  `json:"result,omitempty"`
	Task    *TaskHandle   `json:"task,omitempty"`
	Error   *GatewayError `json:"error,omitempty"`
}

// MediaResponse is the terminal Execution-facing response. It shares only
// backend-neutral observation primitives with LLMResponse; the request and
// result DTOs remain distinct.
type MediaResponse struct {
	Kind          ResponseKind     `json:"kind"`
	SchemaVersion int              `json:"schema_version"`
	OperationID   string           `json:"operation_id"`
	Mode          DeliveryMode     `json:"mode"`
	Terminal      MediaTerminal    `json:"terminal"`
	Observation   MediaObservation `json:"observation"`
	Receipt       ReceiptReference `json:"receipt"`
}
