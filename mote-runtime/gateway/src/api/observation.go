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

// ModelObservation contains the separate model, service, and protocol facts
// frozen into the call plan. Resource values are opaque fingerprints, never
// addresses or secrets.
type ModelObservation struct {
	RequestedBaseModel  string `json:"requested_base_model"`
	ResolvedBaseModel   string `json:"resolved_base_model,omitempty"`
	ServiceKind         string `json:"service_kind"`
	ProtocolID          string `json:"protocol_id"`
	UpstreamRequestID   string `json:"upstream_request_id,omitempty"`
	EndpointFingerprint string `json:"endpoint_fingerprint,omitempty"`
	TenantFingerprint   string `json:"tenant_fingerprint,omitempty"`
	CredentialSlotID    string `json:"credential_slot_id,omitempty"`
}

// FinishObservation records normalized terminal disposition without copying
// upstream-specific response bodies.
type FinishObservation struct {
	Reasons        []FinishReason `json:"reasons"`
	UpstreamReason string         `json:"upstream_reason,omitempty"`
}

// ServiceReportedCost is a monetary cost explicitly reported by the configured
// model service. Gateway may validate, normalize, record, and add reported
// amounts, but it never derives this value from usage, model prices, exchange
// rates, discounts, or customer billing policy. A missing report is represented
// by omitting the containing field, never by an estimate or a fabricated zero.
type ServiceReportedCost struct {
	Currency string `json:"currency"`
	Amount   string `json:"amount"`
}

// AttemptObservation is a terminal summary of one physical upstream attempt.
// It deliberately omits provisional output and stream fragments.
type AttemptObservation struct {
	AttemptID           string               `json:"attempt_id"`
	Ordinal             int64                `json:"ordinal"`
	ResumeGeneration    int64                `json:"resume_generation"`
	State               string               `json:"state"`
	BaseModel           string               `json:"base_model"`
	ServiceKind         string               `json:"service_kind"`
	EndpointFingerprint string               `json:"endpoint_fingerprint,omitempty"`
	CredentialSlotID    string               `json:"credential_slot_id,omitempty"`
	LatencyMS           float64              `json:"latency_ms"`
	Usage               Usage                `json:"usage"`
	ServiceReportedCost *ServiceReportedCost `json:"service_reported_cost,omitempty"`
	Error               *GatewayError        `json:"error,omitempty"`
	Selected            bool                 `json:"selected"`
}

// LLMObservation is the final-only sidecar paired with LLMResponse. Its
// output source is LLMTerminal.Result; there is no duplicate output field.
type LLMObservation struct {
	SchemaVersion       int                  `json:"schema_version"`
	OperationID         string               `json:"operation_id"`
	Operation           Operation            `json:"operation"`
	Modality            Modality             `json:"modality"`
	Mode                DeliveryMode         `json:"mode"`
	Correlation         TraceContext         `json:"correlation"`
	Model               ModelObservation     `json:"model"`
	Input               LLMInput             `json:"input"`
	Finish              FinishObservation    `json:"finish"`
	Usage               Usage                `json:"usage"`
	ServiceReportedCost *ServiceReportedCost `json:"service_reported_cost,omitempty"`
	Timing              StreamSummary        `json:"timing"`
	Cache               CacheObservation     `json:"cache"`
	Attempts            []AttemptObservation `json:"attempts"`
}

// MediaObservation is the corresponding sidecar for MediaResponse. Keeping
// the input type distinct prevents a media DTO from being accepted as an LLM
// request by accident.
type MediaObservation struct {
	SchemaVersion       int                  `json:"schema_version"`
	OperationID         string               `json:"operation_id"`
	Operation           Operation            `json:"operation"`
	Modality            Modality             `json:"modality"`
	Mode                DeliveryMode         `json:"mode"`
	Correlation         TraceContext         `json:"correlation"`
	Model               ModelObservation     `json:"model"`
	Input               MediaInput           `json:"input"`
	Finish              FinishObservation    `json:"finish"`
	Usage               Usage                `json:"usage"`
	ServiceReportedCost *ServiceReportedCost `json:"service_reported_cost,omitempty"`
	Timing              StreamSummary        `json:"timing"`
	Cache               CacheObservation     `json:"cache"`
	Attempts            []AttemptObservation `json:"attempts"`
}
