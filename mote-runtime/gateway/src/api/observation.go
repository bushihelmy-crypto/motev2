package api

// TraceContext carries explicit correlation identities. Gateway does not
// create a second trace tree and never treats a session id as a trace id.
type TraceContext struct {
	TraceID      string `json:"trace_id,omitempty"`
	SessionID    string `json:"session_id,omitempty"`
	ParentSpanID string `json:"parent_span_id,omitempty"`
	GenerationID string `json:"generation_id,omitempty"`
	ModelCallID  string `json:"model_call_id,omitempty"`
}

// ModelObservation contains final model identity facts for Kernel/Execution
// observers. Resource values are opaque fingerprints, never addresses or
// secrets.
type ModelObservation struct {
	RequestedModelID    string `json:"requested_model_id"`
	ResolvedModelID     string `json:"resolved_model_id,omitempty"`
	Provider            string `json:"provider"`
	Protocol            string `json:"protocol"`
	ProviderRequestID   string `json:"provider_request_id,omitempty"`
	EndpointFingerprint string `json:"endpoint_fingerprint,omitempty"`
	TenantFingerprint   string `json:"tenant_fingerprint,omitempty"`
	CredentialSlotID    string `json:"credential_slot_id,omitempty"`
}

// FinishObservation records normalized terminal disposition without copying
// provider-specific response bodies.
type FinishObservation struct {
	Reasons        []FinishReason `json:"reasons"`
	ProviderReason string         `json:"provider_reason,omitempty"`
}

// Money is a decimal string so billing and Langfuse projections avoid binary
// floating-point rounding drift.
type Money struct {
	Currency     string `json:"currency"`
	Amount       string `json:"amount,omitempty"`
	Availability string `json:"availability"`
}

// AttemptObservation is a terminal summary of one physical upstream attempt.
// It deliberately omits provisional output and stream fragments.
type AttemptObservation struct {
	AttemptID           string        `json:"attempt_id"`
	Ordinal             int64         `json:"ordinal"`
	ResumeGeneration    int64         `json:"resume_generation"`
	State               string        `json:"state"`
	ModelID             string        `json:"model_id"`
	Provider            string        `json:"provider"`
	EndpointFingerprint string        `json:"endpoint_fingerprint,omitempty"`
	CredentialSlotID    string        `json:"credential_slot_id,omitempty"`
	LatencyMS           float64       `json:"latency_ms"`
	Usage               Usage         `json:"usage"`
	Cost                Money         `json:"cost"`
	Error               *GatewayError `json:"error,omitempty"`
	Selected            bool          `json:"selected"`
}

// LLMObservation is the final-only sidecar paired with LLMResponse. Its
// output source is LLMTerminal.Result; there is no duplicate output field.
type LLMObservation struct {
	SchemaVersion int                  `json:"schema_version"`
	OperationID   string               `json:"operation_id"`
	Operation     Operation            `json:"operation"`
	Modality      Modality             `json:"modality"`
	Mode          DeliveryMode         `json:"mode"`
	Correlation   TraceContext         `json:"correlation"`
	Model         ModelObservation     `json:"model"`
	Input         LLMInput             `json:"input"`
	Finish        FinishObservation    `json:"finish"`
	Usage         Usage                `json:"usage"`
	Cost          Money                `json:"cost"`
	Timing        StreamSummary        `json:"timing"`
	Cache         CacheObservation     `json:"cache"`
	Attempts      []AttemptObservation `json:"attempts"`
}

// MediaObservation is the corresponding sidecar for MediaResponse. Keeping
// the input type distinct prevents a media DTO from being accepted as an LLM
// request by accident.
type MediaObservation struct {
	SchemaVersion int                  `json:"schema_version"`
	OperationID   string               `json:"operation_id"`
	Operation     Operation            `json:"operation"`
	Modality      Modality             `json:"modality"`
	Mode          DeliveryMode         `json:"mode"`
	Correlation   TraceContext         `json:"correlation"`
	Model         ModelObservation     `json:"model"`
	Input         MediaInput           `json:"input"`
	Finish        FinishObservation    `json:"finish"`
	Usage         Usage                `json:"usage"`
	Cost          Money                `json:"cost"`
	Timing        StreamSummary        `json:"timing"`
	Cache         CacheObservation     `json:"cache"`
	Attempts      []AttemptObservation `json:"attempts"`
}
