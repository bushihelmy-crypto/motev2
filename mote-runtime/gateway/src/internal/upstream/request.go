package upstream

// WireRequest is protocol-owned encoded bytes handed to service authorization.
// Upstream treats the payload as opaque and never interprets model semantics.
type WireRequest interface {
	Bytes() []byte
}

// Target is the service-resolved destination. Credentials and signed headers
// are deliberately not exposed by this contract.
type Target interface {
	Endpoint() string
}

// AuthorizedRequest is the transport-ready result of service authorization.
type AuthorizedRequest interface {
	Target() Target
}
