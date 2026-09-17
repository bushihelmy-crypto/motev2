package api

import (
	"errors"
	"testing"
)

func TestRequestErrorExposesStableMessageAndCause(t *testing.T) {
	cause := errors.New("owner failure")
	withMessage := &RequestError{Code: ErrorInvalidRequest, Message: "invalid request", Cause: cause}
	if withMessage.Error() != "invalid request" || !errors.Is(withMessage, cause) {
		t.Fatalf("request error did not preserve message and cause: %q %v", withMessage.Error(), withMessage.Unwrap())
	}

	withoutMessage := &RequestError{Code: ErrorUnsupported}
	if withoutMessage.Error() != "gateway request failed with UNSUPPORTED_CAPABILITY" || withoutMessage.Unwrap() != nil {
		t.Fatalf("request error fallback changed: %q %v", withoutMessage.Error(), withoutMessage.Unwrap())
	}
}
