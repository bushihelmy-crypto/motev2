package application

import (
	"context"
	"fmt"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/admission"
)

// LLMAdapter is the only unary downstream seam. It receives the immutable
// admitted request, never a raw request, so it cannot silently replace the model,
// protocol, service, or normalized parameters.
type LLMAdapter interface {
	Invoke(context.Context, admission.AdmittedLLM) (api.LLMResponse, error)
}

// MediaAdapter is the corresponding unary Execution seam.
type MediaAdapter interface {
	InvokeMedia(context.Context, admission.AdmittedMedia) (api.MediaResponse, error)
}

// LLMStreamAdapter owns only the stream lifecycle after admission. The
// admitted request is passed through the same application seam as unary calls.
type LLMStreamAdapter[Event any] interface {
	Stream(context.Context, admission.AdmittedLLM) (api.LLMEventStream[Event], error)
}

// MediaStreamAdapter is the media streaming counterpart.
type MediaStreamAdapter[Event any] interface {
	StreamMedia(context.Context, admission.AdmittedMedia) (api.MediaEventStream[Event], error)
}

// LLMRealtimeAdapter opens a caller-owned duplex session from an admitted
// request. Frame delivery and finalization remain owned by the returned session.
type LLMRealtimeAdapter[Input, Output any] interface {
	OpenDuplex(context.Context, admission.AdmittedLLM) (api.DuplexSession[Input, Output], error)
}

// MediaAsyncAdapter submits one already-admitted media request.
type MediaAsyncAdapter interface {
	SubmitMedia(context.Context, admission.AdmittedMedia) (api.TaskHandle, error)
}

// Config composes one immutable admission configuration. Downstream adapters
// are supplied per delivery method so every method still passes through this
// one admission owner and one immutable admitted-request type per profile.
type Config struct {
	Admission admission.Config
}

// Invocation owns the unique admission -> immutable admitted-request chain. It does not
// decode inbound bytes and does not resolve models, protocols, or services.
type Invocation struct {
	validator admission.Validator
}

// New constructs the application pipeline from composition already checked by
// Gateway's public boundary.
func New(config Config) *Invocation {
	return &Invocation{validator: admission.New(config.Admission)}
}

// InvokeLLM admits exactly once and passes only the resulting admitted request downstream.
func (invocation *Invocation) InvokeLLM(ctx context.Context, frame api.LLMRequestFrame, adapter LLMAdapter) (api.LLMResponse, error) {
	admitted, err := invocation.admitLLMForMode(frame, api.ModeUnary)
	if err != nil {
		return api.LLMResponse{}, err
	}
	return adapter.Invoke(ctx, admitted)
}

// InvokeMedia admits exactly once and passes only the resulting admitted request
// downstream.
func (invocation *Invocation) InvokeMedia(ctx context.Context, frame api.MediaRequestFrame, adapter MediaAdapter) (api.MediaResponse, error) {
	admitted, err := invocation.admitMediaForMode(frame, api.ModeUnary)
	if err != nil {
		return api.MediaResponse{}, err
	}
	return adapter.InvokeMedia(ctx, admitted)
}

// OpenLLMStream is the canonical server-stream path. It shares the same
// admission and admitted-request construction code as unary invocation.
func OpenLLMStream[Event any](ctx context.Context, invocation *Invocation, frame api.LLMRequestFrame, adapter LLMStreamAdapter[Event]) (api.LLMEventStream[Event], error) {
	admitted, err := invocation.admitLLMForMode(frame, api.ModeServerStream)
	if err != nil {
		return nil, err
	}
	return adapter.Stream(ctx, admitted)
}

// OpenMediaStream is the canonical media server-stream path.
func OpenMediaStream[Event any](ctx context.Context, invocation *Invocation, frame api.MediaRequestFrame, adapter MediaStreamAdapter[Event]) (api.MediaEventStream[Event], error) {
	admitted, err := invocation.admitMediaForMode(frame, api.ModeServerStream)
	if err != nil {
		return nil, err
	}
	return adapter.StreamMedia(ctx, admitted)
}

// OpenDuplex is the canonical realtime path.
func OpenDuplex[Input, Output any](ctx context.Context, invocation *Invocation, frame api.LLMRequestFrame, adapter LLMRealtimeAdapter[Input, Output]) (api.DuplexSession[Input, Output], error) {
	admitted, err := invocation.admitLLMForMode(frame, api.ModeDuplex)
	if err != nil {
		return nil, err
	}
	return adapter.OpenDuplex(ctx, admitted)
}

// SubmitMedia is the canonical asynchronous media path.
func SubmitMedia(ctx context.Context, invocation *Invocation, frame api.MediaRequestFrame, adapter MediaAsyncAdapter) (api.TaskHandle, error) {
	admitted, err := invocation.admitMediaForMode(frame, api.ModeAsync)
	if err != nil {
		return api.TaskHandle{}, err
	}
	return adapter.SubmitMedia(ctx, admitted)
}

func (invocation *Invocation) admitLLMForMode(frame api.LLMRequestFrame, expected api.DeliveryMode) (admission.AdmittedLLM, error) {
	if err := requireMode(frame.Request.Mode, expected); err != nil {
		return admission.AdmittedLLM{}, err
	}
	admitted, err := invocation.validator.AdmitLLMFrame(frame)
	if err != nil {
		return admission.AdmittedLLM{}, projectAdmissionError(err)
	}
	return admitted, nil
}

func (invocation *Invocation) admitMediaForMode(frame api.MediaRequestFrame, expected api.DeliveryMode) (admission.AdmittedMedia, error) {
	if err := requireMode(frame.Request.Mode, expected); err != nil {
		return admission.AdmittedMedia{}, err
	}
	admitted, err := invocation.validator.AdmitMediaFrame(frame)
	if err != nil {
		return admission.AdmittedMedia{}, projectAdmissionError(err)
	}
	return admitted, nil
}

func projectAdmissionError(err error) error {
	admissionError, ok := admission.AsAdmission(err)
	if !ok {
		return err
	}
	return &api.RequestError{Code: admissionError.Code, Message: err.Error(), Cause: err}
}

func requireMode(actual, expected api.DeliveryMode) error {
	if actual == expected {
		return nil
	}
	return &api.RequestError{
		Code:    api.ErrorInvalidRequest,
		Message: fmt.Sprintf("delivery helper requires mode %q, got %q", expected, actual),
	}
}
