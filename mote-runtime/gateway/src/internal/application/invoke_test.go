package application

import (
	"context"
	"errors"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/admission"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/testkit"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/ports"
)

type recordingLLMAdapter struct {
	calls    int
	admitted admission.AdmittedLLM
}

func (adapter *recordingLLMAdapter) Invoke(_ context.Context, admitted admission.AdmittedLLM) (api.LLMResponse, error) {
	adapter.calls++
	adapter.admitted = admitted
	return api.LLMResponse{OperationID: admitted.Request().OperationID}, nil
}

func TestInvocationAlwaysAdmitsBeforeCallingAdapter(t *testing.T) {
	catalog, err := model.NewCatalogFromRecords([]ports.ModelRecord{{
		BaseModel: "fixture-reasoning",
		Lifecycle: ports.ModelLifecycleActive,
		Capability: ports.ModelCapability{
			Operation:        api.OperationGenerate,
			InputModalities:  []api.Modality{api.ModalityText},
			OutputModalities: []api.Modality{api.ModalityText},
			Reasoning: &ports.ModelReasoning{ThinkingModes: []ports.ModelThinkingMode{
				{Thinking: api.ThinkingDisabled},
				{Thinking: api.ThinkingAdaptive, Efforts: []api.ReasoningEffort{api.ReasoningEffortHigh}},
			}},
		},
	}})
	if err != nil {
		t.Fatalf("construct catalog: %v", err)
	}
	adapter := &recordingLLMAdapter{}
	invocation := New(Config{Admission: admission.Config{Catalog: catalog, Protocol: testkit.ProtocolCapabilities(), Service: testkit.ServiceCapabilities("fixture-reasoning")}})

	request := api.LLMRequest{
		Kind:          api.RequestKindLLM,
		SchemaVersion: 1,
		OperationID:   "op-1",
		BaseModel:     "fixture-reasoning",
		Modality:      api.ModalityText,
		Mode:          api.ModeUnary,
		Input: api.LLMInput{
			Kind:      "generate",
			Messages:  []api.Message{{Role: "user", Content: []api.ContentPart{{Type: "text", Text: "solve"}}}},
			Reasoning: &api.ReasoningConfig{Thinking: api.ThinkingAdaptive, Effort: api.ReasoningEffortHigh},
		},
		Features: []api.Feature{},
	}
	response, err := invocation.InvokeLLM(context.Background(), llmFrame(request), adapter)
	if err != nil || response.OperationID != request.OperationID {
		t.Fatalf("unexpected admitted invocation: %+v %v", response, err)
	}
	if adapter.calls != 1 || adapter.admitted.Request().Operation == nil || *adapter.admitted.Request().Operation != api.OperationGenerate {
		t.Fatalf("adapter did not receive normalized request: calls=%d request=%+v", adapter.calls, adapter.admitted.Request())
	}
	if adapter.admitted.ProtocolID() == "" || adapter.admitted.ServiceKind() == "" {
		t.Fatal("admitted request did not freeze protocol and service identities")
	}

	_, err = invocation.InvokeLLM(context.Background(), llmFrame(api.LLMRequest{
		Kind: api.RequestKindLLM, SchemaVersion: 1, OperationID: "op-2", BaseModel: "fixture-reasoning",
		Modality: api.ModalityText, Mode: api.ModeUnary,
		Input:    api.LLMInput{Kind: "generate", Messages: []api.Message{{Role: "user", Content: []api.ContentPart{{Type: "text", Text: "solve"}}}}, Reasoning: &api.ReasoningConfig{Thinking: api.ThinkingEnabled}},
		Features: []api.Feature{},
	}), adapter)
	var admissionError *admission.AdmissionError
	if !errors.As(err, &admissionError) || admission.Code(err) != api.ErrorUnsupported {
		t.Fatalf("unsupported reasoning did not stop before adapter: %T %v", err, err)
	}
	if adapter.calls != 1 {
		t.Fatalf("adapter was called for rejected request: %d", adapter.calls)
	}
}

func TestInvocationEnforcesDeliveryHelperMode(t *testing.T) {
	catalog, err := model.NewCatalogFromRecords([]ports.ModelRecord{{BaseModel: "mode-fixture", Lifecycle: ports.ModelLifecycleActive, Capability: ports.ModelCapability{
		Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityText},
	}}})
	if err != nil {
		t.Fatalf("construct catalog: %v", err)
	}
	invocation := New(Config{Admission: admission.Config{Catalog: catalog, Protocol: testkit.ProtocolCapabilities(), Service: testkit.ServiceCapabilities("mode-fixture")}})
	adapter := &recordingLLMAdapter{}
	request := api.LLMRequest{
		Kind: api.RequestKindLLM, SchemaVersion: 1, OperationID: "mode-1", BaseModel: "mode-fixture",
		Operation: operationPtr(api.OperationGenerate), Modality: api.ModalityText, Mode: api.ModeServerStream,
		Input:    api.LLMInput{Kind: "generate", Messages: []api.Message{{Role: "user", Content: []api.ContentPart{{Type: "text", Text: "hi"}}}}},
		Features: []api.Feature{},
	}
	_, err = invocation.InvokeLLM(context.Background(), llmFrame(request), adapter)
	var requestError *api.RequestError
	if !errors.As(err, &requestError) || requestError.Code != api.ErrorInvalidRequest {
		t.Fatalf("unary helper accepted a stream mode: %T %v", err, err)
	}
	if adapter.calls != 0 {
		t.Fatalf("mode mismatch reached adapter: %d", adapter.calls)
	}
}

func TestEveryDeliveryPathProjectsAdmissionFailureBeforeAdapter(t *testing.T) {
	catalog, err := model.NewCatalogFromRecords([]ports.ModelRecord{
		{BaseModel: "llm", Lifecycle: ports.ModelLifecycleActive, Capability: ports.ModelCapability{
			Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityText},
		}},
		{BaseModel: "realtime", Lifecycle: ports.ModelLifecycleActive, Capability: ports.ModelCapability{
			Operation: api.OperationRealtime, InputModalities: []api.Modality{api.ModalityAudio}, OutputModalities: []api.Modality{api.ModalityAudio},
		}},
		{BaseModel: "media", Lifecycle: ports.ModelLifecycleActive, Capability: ports.ModelCapability{
			Operation: api.OperationImageGeneration, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityImage},
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	invocation := New(Config{Admission: admission.Config{
		Catalog: catalog, Protocol: testkit.ProtocolCapabilities(), Service: testkit.ServiceCapabilities("llm", "realtime", "media"),
	}})

	invalidLLM := func(baseModel string, operation api.Operation, mode api.DeliveryMode, kind string) api.LLMRequestFrame {
		return api.LLMRequestFrame{Request: api.LLMRequest{
			Kind: api.RequestKindLLM, SchemaVersion: 1, BaseModel: baseModel,
			Operation: operationPtr(operation), Mode: mode, Input: api.LLMInput{Kind: kind}, Features: []api.Feature{},
		}}
	}
	invalidMedia := func(mode api.DeliveryMode) api.MediaRequestFrame {
		return api.MediaRequestFrame{Request: api.MediaRequest{
			Kind: api.RequestKindMedia, SchemaVersion: 1, BaseModel: "media",
			Operation: operationPtr(api.OperationImageGeneration), Mode: mode,
			Input: api.MediaInput{Kind: "image_generation"}, Features: []api.Feature{},
		}}
	}

	if _, err := invocation.InvokeMedia(context.Background(), invalidMedia(api.ModeUnary), nil); err == nil {
		t.Fatal("invalid unary media request reached its adapter")
	}
	if _, err := OpenLLMStream[int](context.Background(), invocation, invalidLLM("llm", api.OperationGenerate, api.ModeServerStream, "generate"), nil); err == nil {
		t.Fatal("invalid LLM stream request reached its adapter")
	}
	if _, err := OpenMediaStream[int](context.Background(), invocation, invalidMedia(api.ModeServerStream), nil); err == nil {
		t.Fatal("invalid media stream request reached its adapter")
	}
	if _, err := OpenDuplex[int, int](context.Background(), invocation, invalidLLM("realtime", api.OperationRealtime, api.ModeDuplex, "realtime"), nil); err == nil {
		t.Fatal("invalid realtime request reached its adapter")
	}
	if _, err := SubmitMedia(context.Background(), invocation, invalidMedia(api.ModeAsync), nil); err == nil {
		t.Fatal("invalid async media request reached its adapter")
	}

	wrongMode := invalidMedia(api.ModeAsync)
	if _, err := invocation.InvokeMedia(context.Background(), wrongMode, nil); err == nil {
		t.Fatal("media helper mode mismatch was accepted")
	}
}

func TestAdmissionProjectionPreservesNonAdmissionError(t *testing.T) {
	want := errors.New("sentinel")
	if got := projectAdmissionError(want); got != want {
		t.Fatalf("non-admission error identity changed: %v", got)
	}
}

func llmFrame(request api.LLMRequest) api.LLMRequestFrame {
	return api.LLMRequestFrame{Request: request}
}

func operationPtr(operation api.Operation) *api.Operation { return &operation }
