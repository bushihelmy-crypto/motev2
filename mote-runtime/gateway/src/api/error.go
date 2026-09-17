package api

import "fmt"

// ErrorCode is stable enough for Kernel policy and recovery decisions.  The
// message is intentionally safe and bounded; raw upstream bodies stay inside
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
// signed headers, raw response body, or upstream-specific retry machinery.
type GatewayError struct {
	Code                ErrorCode `json:"code"`
	Domain              string    `json:"domain"`
	SafeMessage         string    `json:"safe_message,omitempty"`
	UpstreamCode        string    `json:"upstream_code,omitempty"`
	Retryability        string    `json:"retryability"`
	ExternalCommitState string    `json:"external_commit_state"`
}

// RequestError is the Go error projection for deterministic admission and wire
// failures that happen before an invocation can form a terminal response. It
// keeps the stable public code while retaining the owner error for callers
// that need typed diagnostics.
type RequestError struct {
	Code    ErrorCode
	Message string
	Cause   error
}

func (err *RequestError) Error() string {
	if err.Message != "" {
		return err.Message
	}
	return fmt.Sprintf("gateway request failed with %s", err.Code)
}

func (err *RequestError) Unwrap() error { return err.Cause }
