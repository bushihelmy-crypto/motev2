package api

// ErrorCode is stable enough for Kernel policy and recovery decisions.  The
// message is intentionally safe and bounded; raw provider bodies stay inside
// the connector.
type ErrorCode string

const (
	ErrorInvalidRequest      ErrorCode = "INVALID_REQUEST"
	ErrorUnsupported         ErrorCode = "UNSUPPORTED_CAPABILITY"
	ErrorUpstreamRejected    ErrorCode = "UPSTREAM_REJECTED"
	ErrorUpstreamUnavailable ErrorCode = "UPSTREAM_UNAVAILABLE"
	ErrorOutcomeUnknown      ErrorCode = "OUTCOME_UNKNOWN"
	ErrorCancelled           ErrorCode = "CANCELLED"
	ErrorResponseInvalid     ErrorCode = "RESPONSE_INVALID"
)

// GatewayError is the terminal error projection.  It contains no credentials,
// signed headers, raw response body, or provider-specific retry machinery.
type GatewayError struct {
	Code                ErrorCode `json:"code"`
	Domain              string    `json:"domain"`
	SafeMessage         string    `json:"safe_message,omitempty"`
	ProviderCode        string    `json:"provider_code,omitempty"`
	Retryability        string    `json:"retryability"`
	ExternalCommitState string    `json:"external_commit_state"`
}
