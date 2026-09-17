package api

import "context"

// LLMEventStream is a caller-owned delivery lifecycle returned after an LLM
// request has been admitted. Stream events are local delivery values; only
// Finalize produces the terminal DTO.
type LLMEventStream[Event any] interface {
	Recv(context.Context) (Event, error)
	Finalize(context.Context) (LLMResponse, error)
	Close() error
}

// MediaEventStream is the corresponding media lifecycle.
type MediaEventStream[Event any] interface {
	Recv(context.Context) (Event, error)
	Finalize(context.Context) (MediaResponse, error)
	Close() error
}

// DuplexSession is a caller-owned realtime lifecycle. Gateway never owns the
// session's event loop or cancellation path.
type DuplexSession[Input, Output any] interface {
	Send(context.Context, Input) error
	Recv(context.Context) (Output, error)
	Finalize(context.Context) (LLMResponse, error)
	Close() error
}

// LLMRequestFrame is the typed hand-off from invocation infrastructure to
// Gateway admission. The frame carries no raw bytes and cannot be created by
// Gateway's outbound protocol adapters. Each invocation owns one frame; after
// handing it to Gateway, the caller must not mutate its request state.
type LLMRequestFrame struct {
	Request LLMRequest
}

// MediaRequestFrame is the corresponding typed hand-off for Execution media.
// Its request follows the same ownership-transfer rule as LLMRequestFrame.
type MediaRequestFrame struct {
	Request MediaRequest
}
