package gateway_test

import (
	"context"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/ports"
)

type externalLLMAdapter struct{}

func (externalLLMAdapter) Invoke(context.Context, gateway.AdmittedLLM) (api.LLMResponse, error) {
	return api.LLMResponse{}, nil
}

var _ gateway.LLMAdapter = externalLLMAdapter{}

type externalCatalogSource struct {
	records []ports.ModelRecord
}

func (source externalCatalogSource) LoadModelCatalog(context.Context) ([]ports.ModelRecord, error) {
	return source.records, nil
}

func TestPublicCompositionLoadsTheCompleteCatalogSource(t *testing.T) {
	effort := api.ReasoningEffortHigh
	store, err := gateway.NewCatalogStore(context.Background(), externalCatalogSource{records: []ports.ModelRecord{{
		BaseModel: "external-custom-model",
		Lifecycle: ports.ModelLifecycleActive,
		Capability: ports.ModelCapability{
			Operation:        api.OperationGenerate,
			InputModalities:  []api.Modality{api.ModalityText},
			OutputModalities: []api.Modality{api.ModalityText},
			Reasoning: &ports.ModelReasoning{
				ThinkingModes: []ports.ModelThinkingMode{{
					Thinking: api.ThinkingAdaptive,
					Efforts:  []api.ReasoningEffort{effort},
				}},
			},
		},
	}}})
	if err != nil {
		t.Fatalf("construct public catalog store: %v", err)
	}
	definition, err := store.Current().Lookup("external-custom-model")
	if err != nil || definition.Operation() != api.OperationGenerate {
		t.Fatalf("public catalog did not retain custom model: %v %+v", err, definition)
	}
	protocolDescriptor, err := gateway.NewProtocolDescriptor("openai.responses", map[api.Operation]gateway.ProtocolOperationCapability{
		api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}},
	})
	if err != nil {
		t.Fatalf("construct public protocol descriptor: %v", err)
	}
	serviceDescriptor, err := gateway.NewServiceDescriptor(gateway.ServiceDescriptorConfig{
		Kind:      "openai",
		Protocols: []string{"openai.responses"},
		Models:    []string{"external-custom-model"},
		Operations: map[api.Operation]gateway.ServiceOperationCapability{
			api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}},
		},
	})
	if err != nil {
		t.Fatalf("construct public service descriptor: %v", err)
	}
	if _, err := gateway.NewInvocation(gateway.InvocationConfig{
		Catalog: store, Protocol: protocolDescriptor, Service: serviceDescriptor,
	}); err != nil {
		t.Fatalf("compose public invocation: %v", err)
	}
}

func TestPublicCompositionRejectsInvalidDescriptorIdentities(t *testing.T) {
	if _, err := gateway.NewProtocolDescriptor("Bad/Protocol", map[api.Operation]gateway.ProtocolOperationCapability{
		api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}},
	}); err == nil {
		t.Fatal("invalid public protocol identity was accepted")
	}
	if _, err := gateway.NewServiceDescriptor(gateway.ServiceDescriptorConfig{Kind: "Bad/Service"}); err == nil {
		t.Fatal("invalid public service identity was accepted")
	}
}
