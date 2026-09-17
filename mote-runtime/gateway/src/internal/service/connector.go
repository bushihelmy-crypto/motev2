package service

import (
	"context"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/upstream"
)

// CallIdentity is the common immutable identity a connector resolves. The
// request body remains a protocol concern.
type CallIdentity interface {
	BaseModel() string
	ProtocolID() string
	ServiceKind() string
}

// Call is the immutable execution view a future Connector consumes after
// admission. Keeping the selected identities beside the normalized DTO avoids
// handing a connector a raw request that could be rebound to another service.
// Target resolution and authorization remain connector-owned; protocol body
// encoding is intentionally absent here.
type Call interface {
	CallIdentity
	// Request returns the normalized request as a read-only execution value.
	Request() api.MediaRequest
}

// LLMCall is the language-call counterpart. It is separate from Call because
// LLMInput and MediaInput are different DTOs at the api boundary.
type LLMCall interface {
	CallIdentity
	// Request returns the normalized request as a read-only execution value.
	Request() api.LLMRequest
}

// Connector resolves a configured service target, applies service-owned
// authorization to opaque protocol bytes, and classifies service failures. It
// never encodes or decodes a protocol payload.
type Connector interface {
	Facts() Facts
	Resolve(context.Context, CallIdentity) (upstream.Target, error)
	Authorize(context.Context, upstream.Target, upstream.WireRequest) (upstream.AuthorizedRequest, error)
	ClassifyFailure(upstream.Response) error
}
