package protocol

import (
	"context"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

// LLMCall is the immutable execution view supplied by application after
// admission. Identity remains alongside the normalized DTO so a protocol
// adapter cannot silently discard the selected combination.
type LLMCall interface {
	// Request returns the normalized request as a read-only execution value.
	Request() api.LLMRequest
	BaseModel() string
	ProtocolID() string
	ServiceKind() string
}

// MediaCall is the corresponding media execution view.
type MediaCall interface {
	// Request returns the normalized request as a read-only execution value.
	Request() api.MediaRequest
	BaseModel() string
	ProtocolID() string
	ServiceKind() string
}

// LLMAdapter is the outbound protocol seam. It must not accept a raw DTO and
// reconstruct model, protocol, or service selection.
//
// The byte slices are protocol-owned wire payloads. Inbound invocation
// framing is intentionally absent here and remains owned by
// mote-infra/invocation.
type LLMAdapter interface {
	Encode(context.Context, LLMCall) ([]byte, error)
	Decode(context.Context, LLMCall, []byte) (api.LLMResponse, error)
}

// MediaAdapter is the corresponding outbound protocol seam for Execution
// media calls.
type MediaAdapter interface {
	EncodeMedia(context.Context, MediaCall) ([]byte, error)
	DecodeMedia(context.Context, MediaCall, []byte) (api.MediaResponse, error)
}

// StreamDecoder consumes protocol-owned stream bytes for an already admitted
// call and returns the one terminal response. Live event values stay on the
// caller-owned lifecycle and never become a second wire DTO.
type StreamDecoder interface {
	DecodeStream(context.Context, LLMCall, [][]byte) (api.LLMResponse, error)
}
