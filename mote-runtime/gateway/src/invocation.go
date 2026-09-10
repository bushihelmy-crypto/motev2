// Package gateway
//
// The cross-language DTO profiles and terminal-only observation rules live in
// the repository conformance contract. These Go interfaces are the typed local
// adapters for those profiles; they do not define a second wire protocol.
package gateway

import (
	"context"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

// Invocation is the Kernel LLM unary seam. Kernel sends only api.LLMRequest;
// media-generation requests use MediaInvocation and are owned by Execution.
type Invocation interface {
	Invoke(context.Context, api.LLMRequest) (api.LLMResponse, error)
}

// LLMInvocation is the descriptive name for the Kernel model seam.
type LLMInvocation interface {
	Invocation
}

// MediaInvocation is the separate Execution → Gateway media seam. Its DTO is
// intentionally not assignable to Invocation.
type MediaInvocation interface {
	InvokeMedia(context.Context, api.MediaRequest) (api.MediaResponse, error)
}

// LLMStreamInvocation opens a caller-owned LLM delivery stream. Event values
// are local delivery values only; Finalize returns the one terminal response.
type LLMStreamInvocation[Event any] interface {
	Stream(context.Context, api.LLMRequest) (LLMEventStream[Event], error)
}

// MediaStreamInvocation opens a caller-owned media delivery stream. It has a
// distinct request and final response type from the Kernel LLM stream.
type MediaStreamInvocation[Event any] interface {
	StreamMedia(context.Context, api.MediaRequest) (MediaEventStream[Event], error)
}

// LLMInferenceInvocation combines the unary and server-stream LLM forms.
type LLMInferenceInvocation[Event any] interface {
	LLMInvocation
	LLMStreamInvocation[Event]
}

// MediaInferenceInvocation combines synchronous, streaming, and asynchronous
// Execution media forms without widening the Kernel LLM interface.
type MediaInferenceInvocation[Event any] interface {
	MediaInvocation
	MediaStreamInvocation[Event]
	AsyncMediaInvocation
}

// LLMEventStream is one caller-owned LLM stream. Recv is never serialized into
// the Kernel response; Finalize accumulates and normalizes the complete result.
type LLMEventStream[Event any] interface {
	Recv(context.Context) (Event, error)
	Finalize(context.Context) (api.LLMResponse, error)
	Close() error
}

// MediaEventStream is the corresponding Execution media stream lifecycle.
type MediaEventStream[Event any] interface {
	Recv(context.Context) (Event, error)
	Finalize(context.Context) (api.MediaResponse, error)
	Close() error
}

// RealtimeInvocation opens a bidirectional LLM session. The session's final
// response is still terminal-only; frame delivery remains caller-owned.
type RealtimeInvocation[Input, Output any] interface {
	OpenDuplex(context.Context, api.LLMRequest) (DuplexSession[Input, Output], error)
}

// DuplexSession is one caller-owned bidirectional session with one finalizer
// and one idempotent close path.
type DuplexSession[Input, Output any] interface {
	Send(context.Context, Input) error
	Recv(context.Context) (Output, error)
	Finalize(context.Context) (api.LLMResponse, error)
	Close() error
}

// AsyncMediaInvocation submits one Execution media operation and returns a
// durable task handle. Polling and reconciliation are separate operations.
type AsyncMediaInvocation interface {
	SubmitMedia(context.Context, api.MediaRequest) (api.TaskHandle, error)
}

// InvokeStrict forwards exactly one Kernel LLM request without retry,
// fallback, timeout, mutation, or error translation.
func InvokeStrict(
	ctx context.Context,
	invocation Invocation,
	request api.LLMRequest,
) (api.LLMResponse, error) {
	return invocation.Invoke(ctx, request)
}

// InvokeMediaStrict forwards exactly one Execution media request.
func InvokeMediaStrict(
	ctx context.Context,
	invocation MediaInvocation,
	request api.MediaRequest,
) (api.MediaResponse, error) {
	return invocation.InvokeMedia(ctx, request)
}

// StreamStrict opens exactly one caller-owned LLM stream.
func StreamStrict[Event any](
	ctx context.Context,
	invocation LLMStreamInvocation[Event],
	request api.LLMRequest,
) (LLMEventStream[Event], error) {
	return invocation.Stream(ctx, request)
}

// StreamMediaStrict opens exactly one caller-owned media stream.
func StreamMediaStrict[Event any](
	ctx context.Context,
	invocation MediaStreamInvocation[Event],
	request api.MediaRequest,
) (MediaEventStream[Event], error) {
	return invocation.StreamMedia(ctx, request)
}

// OpenDuplexStrict opens exactly one realtime LLM session.
func OpenDuplexStrict[Input, Output any](
	ctx context.Context,
	invocation RealtimeInvocation[Input, Output],
	request api.LLMRequest,
) (DuplexSession[Input, Output], error) {
	return invocation.OpenDuplex(ctx, request)
}

// SubmitMediaStrict submits exactly one asynchronous media operation.
func SubmitMediaStrict(
	ctx context.Context,
	invocation AsyncMediaInvocation,
	request api.MediaRequest,
) (api.TaskHandle, error) {
	return invocation.SubmitMedia(ctx, request)
}
