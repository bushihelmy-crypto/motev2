package gateway

import (
	"context"
	"fmt"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/admission"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/application"
)

// Public aliases expose the immutable admitted values and caller-owned
// lifecycles without defining parallel invocation shapes.
type (
	AdmittedLLM                      = admission.AdmittedLLM
	AdmittedMedia                    = admission.AdmittedMedia
	LLMEventStream[Event any]        = api.LLMEventStream[Event]
	MediaEventStream[Event any]      = api.MediaEventStream[Event]
	DuplexSession[Input, Output any] = api.DuplexSession[Input, Output]
)

// These public names expose the application-owned admitted-request adapter
// seams without defining parallel shapes at the Gateway boundary.
type (
	LLMAdapter                            = application.LLMAdapter
	MediaAdapter                          = application.MediaAdapter
	LLMStreamAdapter[Event any]           = application.LLMStreamAdapter[Event]
	MediaStreamAdapter[Event any]         = application.MediaStreamAdapter[Event]
	LLMRealtimeAdapter[Input, Output any] = application.LLMRealtimeAdapter[Input, Output]
	MediaAsyncAdapter                     = application.MediaAsyncAdapter
)

// Invocation owns the single reusable admission pipeline. Delivery adapters
// are supplied only to the operation that uses them, so stream-only,
// realtime-only, and async-only implementations need no unrelated unary seam.
type Invocation struct {
	pipeline *application.Invocation
}

// NewInvocation constructs the canonical typed-frame admission boundary from
// the already-selected catalog, protocol, and service descriptors.
func NewInvocation(config InvocationConfig) (*Invocation, error) {
	pipeline, err := newPipeline(config)
	if err != nil {
		return nil, err
	}
	return &Invocation{pipeline: pipeline}, nil
}

// InvokeLLM admits one canonical LLM frame and invokes its unary adapter.
func InvokeLLM(ctx context.Context, invocation *Invocation, adapter LLMAdapter, frame api.LLMRequestFrame) (api.LLMResponse, error) {
	if adapter == nil {
		return api.LLMResponse{}, fmt.Errorf("LLM adapter is required")
	}
	return invocation.pipeline.InvokeLLM(ctx, frame, adapter)
}

// InvokeMedia admits one canonical media frame and invokes its unary adapter.
func InvokeMedia(ctx context.Context, invocation *Invocation, adapter MediaAdapter, frame api.MediaRequestFrame) (api.MediaResponse, error) {
	if adapter == nil {
		return api.MediaResponse{}, fmt.Errorf("media adapter is required")
	}
	return invocation.pipeline.InvokeMedia(ctx, frame, adapter)
}

// OpenLLMStream opens one caller-owned LLM stream through the same admission
// pipeline as every other delivery mode.
func OpenLLMStream[Event any](ctx context.Context, invocation *Invocation, adapter LLMStreamAdapter[Event], frame api.LLMRequestFrame) (LLMEventStream[Event], error) {
	if adapter == nil {
		return nil, fmt.Errorf("LLM stream adapter is required")
	}
	return application.OpenLLMStream(ctx, invocation.pipeline, frame, adapter)
}

// OpenMediaStream opens one caller-owned media stream through the shared
// admission pipeline.
func OpenMediaStream[Event any](ctx context.Context, invocation *Invocation, adapter MediaStreamAdapter[Event], frame api.MediaRequestFrame) (MediaEventStream[Event], error) {
	if adapter == nil {
		return nil, fmt.Errorf("media stream adapter is required")
	}
	return application.OpenMediaStream(ctx, invocation.pipeline, frame, adapter)
}

// OpenDuplex opens one caller-owned realtime session after admission.
func OpenDuplex[Input, Output any](ctx context.Context, invocation *Invocation, adapter LLMRealtimeAdapter[Input, Output], frame api.LLMRequestFrame) (DuplexSession[Input, Output], error) {
	if adapter == nil {
		return nil, fmt.Errorf("LLM realtime adapter is required")
	}
	return application.OpenDuplex(ctx, invocation.pipeline, frame, adapter)
}

// SubmitMedia submits one asynchronous media operation after admission.
func SubmitMedia(ctx context.Context, invocation *Invocation, adapter MediaAsyncAdapter, frame api.MediaRequestFrame) (api.TaskHandle, error) {
	if adapter == nil {
		return api.TaskHandle{}, fmt.Errorf("media async adapter is required")
	}
	return application.SubmitMedia(ctx, invocation.pipeline, frame, adapter)
}

func newPipeline(config InvocationConfig) (*application.Invocation, error) {
	if config.Catalog == nil {
		return nil, fmt.Errorf("model catalog is required")
	}
	if !config.Catalog.Current().Ready() {
		return nil, fmt.Errorf("model catalog is not ready")
	}
	if config.Protocol.Name() == "" {
		return nil, fmt.Errorf("protocol descriptor is required")
	}
	if config.Service.Name() == "" {
		return nil, fmt.Errorf("service descriptor is required")
	}
	return application.New(application.Config{Admission: admission.Config{
		Catalog:  config.Catalog,
		Protocol: config.Protocol,
		Service:  config.Service,
	}}), nil
}
