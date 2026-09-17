package gateway

import (
	"context"
	"errors"
	"io"
	"strings"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/admission"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/protocol"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/testkit"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/ports"
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

func (s *invocationTestSession) Send(context.Context, invocationTestEvent) error { return nil }

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
	stream            *invocationTestStream
	session           *invocationTestSession
	mediaStream       *invocationTestMediaStream
	lastContext       context.Context
	lastAdmitted      AdmittedLLM
	lastMediaAdmitted AdmittedMedia
	mediaCalls        int
	mediaStreamCalls  int
	mediaSubmitCalls  int
	calls             int
}

type llmStreamOnlyAdapter struct {
	stream LLMEventStream[invocationTestEvent]
}

func (adapter llmStreamOnlyAdapter) Stream(context.Context, AdmittedLLM) (LLMEventStream[invocationTestEvent], error) {
	return adapter.stream, nil
}

type mediaStreamOnlyAdapter struct {
	stream MediaEventStream[invocationTestEvent]
}

func (adapter mediaStreamOnlyAdapter) StreamMedia(context.Context, AdmittedMedia) (MediaEventStream[invocationTestEvent], error) {
	return adapter.stream, nil
}

type realtimeOnlyAdapter struct {
	session DuplexSession[invocationTestEvent, invocationTestEvent]
}

func (adapter realtimeOnlyAdapter) OpenDuplex(context.Context, AdmittedLLM) (DuplexSession[invocationTestEvent, invocationTestEvent], error) {
	return adapter.session, nil
}

type asyncOnlyAdapter struct{}

func (asyncOnlyAdapter) SubmitMedia(_ context.Context, admitted AdmittedMedia) (api.TaskHandle, error) {
	return api.TaskHandle{TaskID: "async-only", OperationID: admitted.Request().OperationID}, nil
}

func (s *invocationTestService) Invoke(ctx context.Context, admitted AdmittedLLM) (api.LLMResponse, error) {
	s.record(ctx, admitted)
	return api.LLMResponse{OperationID: admitted.Request().OperationID}, nil
}

func (s *invocationTestService) Stream(ctx context.Context, admitted AdmittedLLM) (LLMEventStream[invocationTestEvent], error) {
	s.record(ctx, admitted)
	return s.stream, nil
}

func (s *invocationTestService) OpenDuplex(ctx context.Context, admitted AdmittedLLM) (DuplexSession[invocationTestEvent, invocationTestEvent], error) {
	s.record(ctx, admitted)
	return s.session, nil
}

func (s *invocationTestService) InvokeMedia(_ context.Context, admitted AdmittedMedia) (api.MediaResponse, error) {
	s.mediaCalls++
	s.lastMediaAdmitted = admitted
	return api.MediaResponse{OperationID: admitted.Request().OperationID}, nil
}

func (s *invocationTestService) StreamMedia(_ context.Context, admitted AdmittedMedia) (MediaEventStream[invocationTestEvent], error) {
	s.mediaStreamCalls++
	s.lastMediaAdmitted = admitted
	return s.mediaStream, nil
}

func (s *invocationTestService) SubmitMedia(_ context.Context, admitted AdmittedMedia) (api.TaskHandle, error) {
	s.mediaSubmitCalls++
	s.lastMediaAdmitted = admitted
	return api.TaskHandle{TaskID: "task-1", OperationID: admitted.Request().OperationID}, nil
}

func (s *invocationTestService) record(ctx context.Context, admitted AdmittedLLM) {
	s.calls++
	s.lastContext = ctx
	s.lastAdmitted = admitted
}

func TestInvocationUsesOneAdmittedChainWithoutOwningLifecycle(t *testing.T) {
	stream := &invocationTestStream{}
	mediaStream := &invocationTestMediaStream{}
	session := &invocationTestSession{}
	service := &invocationTestService{stream: stream, mediaStream: mediaStream, session: session}
	config := testInvocationConfig(t)
	invocation, err := NewInvocation(config)
	if err != nil {
		t.Fatalf("construct invocation: %v", err)
	}
	ctx := context.WithValue(context.Background(), invocationTestContextKey{}, "test")
	request := validLLMRequest("chatgpt-4o", api.OperationGenerate, api.ModalityText, api.ModeUnary, "generate", "op-1")
	var _ LLMAdapter = service
	var _ LLMStreamAdapter[invocationTestEvent] = service
	var _ LLMRealtimeAdapter[invocationTestEvent, invocationTestEvent] = service
	var _ MediaAdapter = service
	var _ MediaStreamAdapter[invocationTestEvent] = service
	var _ MediaAsyncAdapter = service

	result, err := InvokeLLM(ctx, invocation, service, llmFrame(request))
	if err != nil || result.OperationID != request.OperationID {
		t.Fatalf("unexpected unary result: %+v, %v", result, err)
	}
	streamRequest := validLLMRequest("chatgpt-4o", api.OperationGenerate, api.ModalityText, api.ModeServerStream, "generate", "op-stream")
	opened, err := OpenLLMStream(ctx, invocation, service, llmFrame(streamRequest))
	if err != nil || opened != stream {
		t.Fatalf("unexpected stream: %v, %v", opened, err)
	}
	duplexRequest := validLLMRequest("gpt-realtime-1.5", api.OperationRealtime, api.ModalityAudio, api.ModeDuplex, "realtime", "op-duplex")
	duplex, err := OpenDuplex(ctx, invocation, service, llmFrame(duplexRequest))
	if err != nil || duplex != session {
		t.Fatalf("unexpected duplex session: %v, %v", duplex, err)
	}
	mediaRequest := validMediaRequest("dall-e-3", api.OperationImageGeneration, api.ModalityImage, api.ModeUnary, "media-1")
	if _, err = InvokeMedia(ctx, invocation, service, mediaFrame(mediaRequest)); err != nil {
		t.Fatalf("unexpected media invocation error: %v", err)
	}
	mediaStreamRequest := validMediaRequest("dall-e-3", api.OperationImageGeneration, api.ModalityImage, api.ModeServerStream, "media-stream")
	mediaOpened, err := OpenMediaStream(ctx, invocation, service, mediaFrame(mediaStreamRequest))
	if err != nil || mediaOpened != mediaStream {
		t.Fatalf("unexpected media stream: %v, %v", mediaOpened, err)
	}
	mediaAsyncRequest := validMediaRequest("dall-e-3", api.OperationImageGeneration, api.ModalityImage, api.ModeAsync, "media-async")
	task, err := SubmitMedia(ctx, invocation, service, mediaFrame(mediaAsyncRequest))
	if err != nil || task.TaskID != "task-1" {
		t.Fatalf("unexpected media task: %+v, %v", task, err)
	}
	if service.calls != 3 || service.mediaCalls != 1 {
		t.Fatalf("expected three Kernel calls and one media call, got Kernel=%d media=%d", service.calls, service.mediaCalls)
	}
	if service.mediaStreamCalls != 1 || service.mediaSubmitCalls != 1 {
		t.Fatalf("expected one media stream and one media submission, got stream=%d submit=%d", service.mediaStreamCalls, service.mediaSubmitCalls)
	}
	if service.lastContext != ctx || service.lastAdmitted.Request().Operation == nil || duplexRequest.Operation == nil || *service.lastAdmitted.Request().Operation != *duplexRequest.Operation || service.lastAdmitted.Request().OperationID != duplexRequest.OperationID {
		t.Fatal("strict helpers must forward context and the normalized admitted identity")
	}
	if service.lastAdmitted.Model().BaseModel() != duplexRequest.BaseModel || service.lastAdmitted.ProtocolID() == "" || service.lastAdmitted.ServiceKind() == "" {
		t.Fatal("downstream adapter did not receive the frozen model/protocol/service identity")
	}
	if stream.closed || stream.finalized || session.closed || session.finalized || mediaStream.closed || mediaStream.finalized {
		t.Fatal("strict helpers must not take ownership of returned lifecycle")
	}
}

func TestDeliverySpecificAdaptersNeedNoUnaryAdapter(t *testing.T) {
	invocation, err := NewInvocation(testInvocationConfig(t))
	if err != nil {
		t.Fatal(err)
	}
	llmStream := &invocationTestStream{}
	mediaStream := &invocationTestMediaStream{}
	session := &invocationTestSession{}
	var _ LLMStreamAdapter[invocationTestEvent] = llmStreamOnlyAdapter{}
	var _ MediaStreamAdapter[invocationTestEvent] = mediaStreamOnlyAdapter{}
	var _ LLMRealtimeAdapter[invocationTestEvent, invocationTestEvent] = realtimeOnlyAdapter{}
	var _ MediaAsyncAdapter = asyncOnlyAdapter{}

	llmRequest := validLLMRequest("chatgpt-4o", api.OperationGenerate, api.ModalityText, api.ModeServerStream, "generate", "stream-only")
	openedLLM, err := OpenLLMStream(context.Background(), invocation, llmStreamOnlyAdapter{stream: llmStream}, llmFrame(llmRequest))
	if err != nil || openedLLM != llmStream {
		t.Fatalf("stream-only LLM adapter failed: %v %v", openedLLM, err)
	}
	mediaRequest := validMediaRequest("dall-e-3", api.OperationImageGeneration, api.ModalityImage, api.ModeServerStream, "media-stream-only")
	openedMedia, err := OpenMediaStream(context.Background(), invocation, mediaStreamOnlyAdapter{stream: mediaStream}, mediaFrame(mediaRequest))
	if err != nil || openedMedia != mediaStream {
		t.Fatalf("stream-only media adapter failed: %v %v", openedMedia, err)
	}
	realtimeRequest := validLLMRequest("gpt-realtime-1.5", api.OperationRealtime, api.ModalityAudio, api.ModeDuplex, "realtime", "realtime-only")
	openedSession, err := OpenDuplex(context.Background(), invocation, realtimeOnlyAdapter{session: session}, llmFrame(realtimeRequest))
	if err != nil || openedSession != session {
		t.Fatalf("realtime-only adapter failed: %v %v", openedSession, err)
	}
	asyncRequest := validMediaRequest("dall-e-3", api.OperationImageGeneration, api.ModalityImage, api.ModeAsync, "async-only")
	task, err := SubmitMedia(context.Background(), invocation, asyncOnlyAdapter{}, mediaFrame(asyncRequest))
	if err != nil || task.TaskID != "async-only" {
		t.Fatalf("async-only adapter failed: %+v %v", task, err)
	}
}

func TestInvocationPreservesImplementationError(t *testing.T) {
	want := errors.New("sentinel")
	invocation := &failingInvocation{err: want}
	pipeline, constructErr := NewInvocation(testInvocationConfig(t))
	if constructErr != nil {
		t.Fatal(constructErr)
	}
	_, err := InvokeLLM(context.Background(), pipeline, invocation, llmFrame(validLLMRequest("chatgpt-4o", api.OperationGenerate, api.ModalityText, api.ModeUnary, "generate", "op-error")))
	if !errors.Is(err, want) {
		t.Fatalf("implementation error was not preserved: %v", err)
	}
	if invocation.calls != 1 {
		t.Fatalf("expected one invocation, got %d", invocation.calls)
	}
}

func TestAdapterAdmissionShapedErrorIsNotReclassified(t *testing.T) {
	downstream := &admission.AdmissionError{Code: api.ErrorInvalidRequest, Err: errors.New("downstream sentinel")}
	invocation := &failingInvocation{err: downstream}

	pipeline, constructErr := NewInvocation(testInvocationConfig(t))
	if constructErr != nil {
		t.Fatal(constructErr)
	}
	_, err := InvokeLLM(context.Background(), pipeline, invocation, llmFrame(validLLMRequest("chatgpt-4o", api.OperationGenerate, api.ModalityText, api.ModeUnary, "generate", "op-downstream")))
	if err != downstream {
		t.Fatalf("adapter error identity changed: %T %v", err, err)
	}
	var requestError *api.RequestError
	if errors.As(err, &requestError) {
		t.Fatalf("adapter error was reclassified as a request error: %+v", requestError)
	}
}

func TestDeliveryHelperInvariantPrecedesDescriptorCapabilities(t *testing.T) {
	request := validLLMRequest("chatgpt-4o", api.OperationGenerate, api.ModalityText, api.ModeServerStream, "generate", "op-mode-priority")
	configs := []InvocationConfig{testInvocationConfig(t), testInvocationConfig(t)}
	narrow, err := protocol.NewDescriptor("protocol.unary", map[api.Operation]protocol.OperationCapability{
		api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}},
	})
	if err != nil {
		t.Fatalf("construct narrow protocol: %v", err)
	}
	configs[1].Protocol = narrow
	for index, config := range configs {
		adapter := &invocationTestService{}
		invocation, constructErr := NewInvocation(config)
		if constructErr != nil {
			t.Fatal(constructErr)
		}
		_, err := InvokeLLM(context.Background(), invocation, adapter, llmFrame(request))
		var requestError *api.RequestError
		if !errors.As(err, &requestError) || requestError.Code != api.ErrorInvalidRequest {
			t.Fatalf("config %d changed helper invariant error: %T %v", index, err, err)
		}
		if adapter.calls != 0 {
			t.Fatalf("config %d reached adapter on helper mismatch", index)
		}
	}
}

func TestInvocationMapsAdmissionFailuresToRequestError(t *testing.T) {
	adapter := &invocationTestService{}
	config := testInvocationConfig(t)
	request := validLLMRequest("chatgpt-4o", api.OperationGenerate, api.ModalityText, api.ModeUnary, "generate", "op-invalid")
	request.Input.Messages = nil

	invocation, constructErr := NewInvocation(config)
	if constructErr != nil {
		t.Fatal(constructErr)
	}
	_, err := InvokeLLM(context.Background(), invocation, adapter, llmFrame(request))
	var requestError *api.RequestError
	if !errors.As(err, &requestError) || requestError.Code != api.ErrorInvalidRequest {
		t.Fatalf("expected public invalid-request projection, got %T %v", err, err)
	}
	if adapter.calls != 0 {
		t.Fatalf("adapter was called for an admission failure: %d", adapter.calls)
	}
}

func TestInvocationUsesBoundProtocolAndServiceCapabilities(t *testing.T) {
	narrow, err := protocol.NewDescriptor("protocol.server-stream-only", map[api.Operation]protocol.OperationCapability{
		api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeServerStream}},
	})
	if err != nil {
		t.Fatalf("construct protocol descriptor: %v", err)
	}
	adapter := &invocationTestService{}
	config := testInvocationConfig(t)
	config.Protocol = narrow
	invocation, constructErr := NewInvocation(config)
	if constructErr != nil {
		t.Fatal(constructErr)
	}
	_, err = InvokeLLM(context.Background(), invocation, adapter, llmFrame(validLLMRequest("chatgpt-4o", api.OperationGenerate, api.ModalityText, api.ModeUnary, "generate", "op-narrow")))
	var requestError *api.RequestError
	if !errors.As(err, &requestError) || requestError.Code != api.ErrorUnsupported {
		t.Fatalf("expected bound protocol rejection, got %T %v", err, err)
	}
	if adapter.calls != 0 {
		t.Fatalf("capability rejection reached adapter: %d", adapter.calls)
	}
}

func TestInvocationRejectsIncompleteComposition(t *testing.T) {
	_, err := NewInvocation(InvocationConfig{})
	if err == nil || !strings.Contains(err.Error(), "model catalog is required") {
		t.Fatalf("expected explicit composition error, got %v", err)
	}
}

func TestInvokeLLMRejectsMissingAdapter(t *testing.T) {
	invocation, err := NewInvocation(testInvocationConfig(t))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := InvokeLLM(context.Background(), invocation, nil, api.LLMRequestFrame{}); err == nil {
		t.Fatal("missing invocation target was accepted")
	}
}

func TestInvocationUsesPointerOperationPresence(t *testing.T) {
	adapter := &invocationTestService{}
	config := testInvocationConfig(t)
	invocation, err := NewInvocation(config)
	if err != nil {
		t.Fatal(err)
	}
	request := validLLMRequest("chatgpt-4o", "", api.ModalityText, api.ModeUnary, "generate", "frame-1")
	request.Operation = nil
	frame := api.LLMRequestFrame{Request: request}
	if _, err := InvokeLLM(context.Background(), invocation, adapter, frame); err != nil {
		t.Fatalf("omitted operation should use the catalog default: %v", err)
	}
	if adapter.lastAdmitted.Request().Operation == nil || *adapter.lastAdmitted.Request().Operation != api.OperationGenerate {
		t.Fatalf("frame admission did not materialize operation: %v", adapter.lastAdmitted.Request().Operation)
	}

	emptyOperation := api.Operation("")
	emptyRequest := request
	emptyRequest.Operation = &emptyOperation
	empty := api.LLMRequestFrame{Request: emptyRequest}
	_, err = InvokeLLM(context.Background(), invocation, adapter, empty)
	var requestError *api.RequestError
	if !errors.As(err, &requestError) || requestError.Code != api.ErrorInvalidRequest {
		t.Fatalf("explicit empty operation should be invalid: %T %v", err, err)
	}
	if adapter.calls != 1 {
		t.Fatalf("frame validation failure reached adapter: %d", adapter.calls)
	}
}

func testInvocationConfig(t *testing.T) InvocationConfig {
	t.Helper()
	catalog, err := model.NewCatalogFromRecords([]ports.ModelRecord{
		fixtureModelRecord("chatgpt-4o", api.OperationGenerate, []api.Modality{api.ModalityText}, []api.Modality{api.ModalityText}),
		fixtureModelRecord("gpt-realtime-1.5", api.OperationRealtime, []api.Modality{api.ModalityText, api.ModalityAudio}, []api.Modality{api.ModalityText, api.ModalityAudio}),
		fixtureModelRecord("dall-e-3", api.OperationImageGeneration, []api.Modality{api.ModalityText}, []api.Modality{api.ModalityImage}),
	})
	if err != nil {
		t.Fatalf("construct test catalog: %v", err)
	}
	return InvocationConfig{
		Catalog:  catalog,
		Protocol: testkit.ProtocolCapabilities(),
		Service:  testkit.ServiceCapabilities(),
	}
}

func TestInvocationConfigInjectsCompleteCatalogRecord(t *testing.T) {
	defaultThinking := api.ThinkingAdaptive
	effort := api.ReasoningEffortHigh
	catalog, err := model.NewCatalogFromRecords([]ports.ModelRecord{{
		BaseModel: "custom-reasoning",
		Lifecycle: ports.ModelLifecycleActive,
		Capability: ports.ModelCapability{
			Operation:        api.OperationGenerate,
			InputModalities:  []api.Modality{api.ModalityText},
			OutputModalities: []api.Modality{api.ModalityText},
			Reasoning: &ports.ModelReasoning{
				ThinkingModes:   []ports.ModelThinkingMode{{Thinking: api.ThinkingAdaptive, Efforts: []api.ReasoningEffort{effort}}},
				DefaultThinking: &defaultThinking,
			},
		},
	}})
	if err != nil {
		t.Fatalf("construct catalog: %v", err)
	}
	adapter := &invocationTestService{}
	request := validLLMRequest("custom-reasoning", api.OperationGenerate, api.ModalityText, api.ModeUnary, "generate", "custom-1")
	request.Input.Reasoning = &api.ReasoningConfig{Thinking: api.ThinkingAdaptive, Effort: effort}
	invocation, constructErr := NewInvocation(InvocationConfig{
		Catalog:  catalog,
		Protocol: testkit.ProtocolCapabilities(),
		Service:  testkit.ServiceCapabilities("custom-reasoning"),
	})
	if constructErr != nil {
		t.Fatal(constructErr)
	}
	if _, err := InvokeLLM(context.Background(), invocation, adapter, llmFrame(request)); err != nil {
		t.Fatalf("catalog record did not reach admission: %v", err)
	}
	if adapter.calls != 1 || adapter.lastAdmitted.Model().BaseModel() != "custom-reasoning" {
		t.Fatal("catalog model was not frozen into the downstream admitted request")
	}
}

func fixtureModelRecord(baseModel string, operation api.Operation, inputs, outputs []api.Modality) ports.ModelRecord {
	return ports.ModelRecord{
		BaseModel: baseModel,
		Lifecycle: ports.ModelLifecycleActive,
		Capability: ports.ModelCapability{
			Operation: operation, InputModalities: inputs, OutputModalities: outputs,
		},
	}
}

func validLLMRequest(baseModel string, operation api.Operation, modality api.Modality, mode api.DeliveryMode, kind, operationID string) api.LLMRequest {
	input := api.LLMInput{Kind: kind}
	if kind == "generate" {
		input.Messages = []api.Message{{Role: "user", Content: []api.ContentPart{{Type: "text", Text: "hello"}}}}
	}
	return api.LLMRequest{
		Kind:          api.RequestKindLLM,
		SchemaVersion: 1,
		OperationID:   operationID,
		BaseModel:     baseModel,
		Operation:     operationPtr(operation),
		Modality:      modality,
		Mode:          mode,
		Input:         input,
		Features:      []api.Feature{},
	}
}

func validMediaRequest(baseModel string, operation api.Operation, modality api.Modality, mode api.DeliveryMode, operationID string) api.MediaRequest {
	return api.MediaRequest{
		Kind:          api.RequestKindMedia,
		SchemaVersion: 1,
		OperationID:   operationID,
		BaseModel:     baseModel,
		Operation:     operationPtr(operation),
		Modality:      modality,
		Mode:          mode,
		Input:         api.MediaInput{Kind: "image_generation", Prompt: "a mote"},
		Features:      []api.Feature{},
	}
}

func llmFrame(request api.LLMRequest) api.LLMRequestFrame {
	return api.LLMRequestFrame{Request: request}
}

func mediaFrame(request api.MediaRequest) api.MediaRequestFrame {
	return api.MediaRequestFrame{Request: request}
}

func operationPtr(operation api.Operation) *api.Operation { return &operation }

type failingInvocation struct {
	err   error
	calls int
}

func (i *failingInvocation) Invoke(context.Context, AdmittedLLM) (api.LLMResponse, error) {
	i.calls++
	return api.LLMResponse{}, i.err
}
