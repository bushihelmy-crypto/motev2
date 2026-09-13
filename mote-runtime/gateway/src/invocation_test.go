package gateway

import (
	"context"
	"errors"
	"io"
	"reflect"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

type invocationTestEvent struct{ Kind string }

type invocationTestContextKey struct{}

type invocationTestStream struct {
	closed    bool
	finalized bool
}

type invocationTestMediaStream struct {
	closed    bool
	finalized bool
}

func (s *invocationTestStream) Recv(context.Context) (invocationTestEvent, error) {
	return invocationTestEvent{}, io.EOF
}

func (s *invocationTestStream) Finalize(context.Context) (api.LLMResponse, error) {
	s.finalized = true
	return api.LLMResponse{OperationID: "op-final"}, nil
}

func (s *invocationTestStream) Close() error {
	s.closed = true
	return nil
}

func (s *invocationTestMediaStream) Recv(context.Context) (invocationTestEvent, error) {
	return invocationTestEvent{}, io.EOF
}

func (s *invocationTestMediaStream) Finalize(context.Context) (api.MediaResponse, error) {
	s.finalized = true
	return api.MediaResponse{OperationID: "media-final"}, nil
}

func (s *invocationTestMediaStream) Close() error {
	s.closed = true
	return nil
}

type invocationTestSession struct {
	closed    bool
	finalized bool
}

func (s *invocationTestSession) Send(context.Context, invocationTestEvent) error {
	return nil
}

func (s *invocationTestSession) Recv(context.Context) (invocationTestEvent, error) {
	return invocationTestEvent{}, io.EOF
}

func (s *invocationTestSession) Finalize(context.Context) (api.LLMResponse, error) {
	s.finalized = true
	return api.LLMResponse{OperationID: "op-final"}, nil
}

func (s *invocationTestSession) Close() error {
	s.closed = true
	return nil
}

type invocationTestService struct {
	stream           *invocationTestStream
	session          *invocationTestSession
	mediaStream      *invocationTestMediaStream
	lastContext      context.Context
	lastRequest      api.LLMRequest
	mediaCalls       int
	mediaStreamCalls int
	mediaSubmitCalls int
	calls            int
}

func (s *invocationTestService) Invoke(ctx context.Context, request api.LLMRequest) (api.LLMResponse, error) {
	s.record(ctx, request)
	return api.LLMResponse{OperationID: "op-unary"}, nil
}

func (s *invocationTestService) Stream(ctx context.Context, request api.LLMRequest) (LLMEventStream[invocationTestEvent], error) {
	s.record(ctx, request)
	return s.stream, nil
}

func (s *invocationTestService) OpenDuplex(ctx context.Context, request api.LLMRequest) (DuplexSession[invocationTestEvent, invocationTestEvent], error) {
	s.record(ctx, request)
	return s.session, nil
}

func (s *invocationTestService) InvokeMedia(context.Context, api.MediaRequest) (api.MediaResponse, error) {
	s.mediaCalls++
	return api.MediaResponse{}, nil
}

func (s *invocationTestService) StreamMedia(context.Context, api.MediaRequest) (MediaEventStream[invocationTestEvent], error) {
	s.mediaStreamCalls++
	return s.mediaStream, nil
}

func (s *invocationTestService) SubmitMedia(context.Context, api.MediaRequest) (api.TaskHandle, error) {
	s.mediaSubmitCalls++
	return api.TaskHandle{TaskID: "task-1"}, nil
}

func (s *invocationTestService) record(ctx context.Context, request api.LLMRequest) {
	s.calls++
	s.lastContext = ctx
	s.lastRequest = request
}

func TestInvocationStrictHelpersForwardOneCallWithoutOwningLifecycle(t *testing.T) {
	stream := &invocationTestStream{}
	mediaStream := &invocationTestMediaStream{}
	session := &invocationTestSession{}
	service := &invocationTestService{stream: stream, mediaStream: mediaStream, session: session}
	ctx := context.WithValue(context.Background(), invocationTestContextKey{}, "test")
	request := api.LLMRequest{OperationID: "op-1"}
	var _ Invocation = service
	var _ LLMInvocation = service
	var _ LLMInferenceInvocation[invocationTestEvent] = service
	var _ MediaInferenceInvocation[invocationTestEvent] = service
	var _ RealtimeInvocation[invocationTestEvent, invocationTestEvent] = service
	var _ MediaInvocation = service
	var _ MediaStreamInvocation[invocationTestEvent] = service
	var _ AsyncMediaInvocation = service

	result, err := InvokeStrict(ctx, service, request)
	if err != nil || result.OperationID != "op-unary" {
		t.Fatalf("unexpected unary result: %+v, %v", result, err)
	}
	opened, err := StreamStrict(ctx, service, request)
	if err != nil || opened != stream {
		t.Fatalf("unexpected stream: %v, %v", opened, err)
	}
	duplex, err := OpenDuplexStrict(ctx, service, request)
	if err != nil || duplex != session {
		t.Fatalf("unexpected duplex session: %v, %v", duplex, err)
	}
	mediaRequest := api.MediaRequest{OperationID: "media-1"}
	if _, err = InvokeMediaStrict(ctx, service, mediaRequest); err != nil {
		t.Fatalf("unexpected media invocation error: %v", err)
	}
	mediaOpened, err := StreamMediaStrict(ctx, service, mediaRequest)
	if err != nil || mediaOpened != mediaStream {
		t.Fatalf("unexpected media stream: %v, %v", mediaOpened, err)
	}
	task, err := SubmitMediaStrict(ctx, service, mediaRequest)
	if err != nil || task.TaskID != "task-1" {
		t.Fatalf("unexpected media task: %+v, %v", task, err)
	}
	if service.calls != 3 || service.mediaCalls != 1 {
		t.Fatalf("expected three Kernel calls and one media call, got Kernel=%d media=%d", service.calls, service.mediaCalls)
	}
	if service.mediaStreamCalls != 1 || service.mediaSubmitCalls != 1 {
		t.Fatalf("expected one media stream and one media submission, got stream=%d submit=%d", service.mediaStreamCalls, service.mediaSubmitCalls)
	}
	if service.lastContext != ctx || !reflect.DeepEqual(service.lastRequest, request) {
		t.Fatal("strict helpers must forward context and request unchanged")
	}
	if stream.closed || stream.finalized || session.closed || session.finalized || mediaStream.closed || mediaStream.finalized {
		t.Fatal("strict helpers must not take ownership of returned lifecycle")
	}
}

func TestInvokeStrictPreservesImplementationError(t *testing.T) {
	want := errors.New("sentinel")
	invocation := &failingInvocation{err: want}

	_, err := InvokeStrict(context.Background(), invocation, api.LLMRequest{})
	if !errors.Is(err, want) {
		t.Fatalf("implementation error was not preserved: %v", err)
	}
	if invocation.calls != 1 {
		t.Fatalf("expected one invocation, got %d", invocation.calls)
	}
}

type failingInvocation struct {
	err   error
	calls int
}

func (i *failingInvocation) Invoke(context.Context, api.LLMRequest) (api.LLMResponse, error) {
	i.calls++
	return api.LLMResponse{}, i.err
}
