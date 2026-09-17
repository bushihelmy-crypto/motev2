package service

import (
	"strings"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

func TestDescriptorFreezesServiceCapabilities(t *testing.T) {
	modes := []api.DeliveryMode{api.ModeUnary}
	features := []api.Feature{api.FeatureStructured}
	efforts := []api.ReasoningEffort{api.ReasoningEffortMedium}
	reasoning := []ReasoningCapability{{Thinking: api.ThinkingEnabled, Efforts: efforts}}
	protocols := []string{"protocol.test"}
	models := []string{"model.test"}
	descriptor, err := NewDescriptor(DescriptorConfig{Kind: "service.test", Protocols: protocols, Models: models, Operations: map[api.Operation]OperationCapability{
		api.OperationGenerate: {Modes: modes, Features: features, Reasoning: reasoning},
	}})
	if err != nil {
		t.Fatalf("construct descriptor: %v", err)
	}

	modes[0] = api.ModeAsync
	features[0] = api.FeatureUsage
	efforts[0] = api.ReasoningEffortHigh
	reasoning[0].Thinking = api.ThinkingDisabled
	protocols[0] = "protocol.changed"
	models[0] = "model.changed"

	if !descriptor.SupportsProtocol("protocol.test") || descriptor.SupportsProtocol("protocol.changed") ||
		!descriptor.SupportsModel("model.test") || descriptor.SupportsModel("model.changed") ||
		!descriptor.SupportsOperation(api.OperationGenerate, api.ModeUnary) ||
		!descriptor.SupportsFeature(api.OperationGenerate, api.FeatureStructured) ||
		!descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingEnabled, Effort: api.ReasoningEffortMedium}) {
		t.Fatal("descriptor retained caller-owned capability slices")
	}
	if descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingEnabled, Effort: api.ReasoningEffortHigh}) {
		t.Fatal("descriptor accepted an undeclared reasoning effort")
	}
}

func TestDescriptorRejectsMissingServiceIdentityOrOperations(t *testing.T) {
	operation := map[api.Operation]OperationCapability{api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}}}
	if _, err := NewDescriptor(DescriptorConfig{Kind: "", Protocols: []string{"protocol.test"}, Models: []string{"model.test"}, Operations: operation}); err == nil {
		t.Fatal("empty service identity was accepted")
	}
	if _, err := NewDescriptor(DescriptorConfig{Kind: "service.test", Protocols: []string{"protocol.test"}, Models: []string{"model.test"}, Operations: nil}); err == nil {
		t.Fatal("service without operations was accepted")
	}
	if _, err := NewDescriptor(DescriptorConfig{Kind: "service.test", Protocols: []string{"protocol.test"}, Operations: operation}); err == nil {
		t.Fatal("service without exact model deployment was accepted")
	}
	if _, err := NewDescriptor(DescriptorConfig{Kind: "service.test", Protocols: nil, Models: []string{"model.test"}, Operations: operation}); err == nil {
		t.Fatal("service without protocols was accepted")
	}
	if _, err := NewDescriptor(DescriptorConfig{Kind: "service.test", Protocols: []string{""}, Models: []string{"model.test"}, Operations: operation}); err == nil {
		t.Fatal("service with an empty protocol was accepted")
	}
	if _, err := NewDescriptor(DescriptorConfig{Kind: "service.test", Protocols: []string{"protocol.test", "protocol.test"}, Models: []string{"model.test"}, Operations: operation}); err == nil {
		t.Fatal("service with duplicate protocols was accepted")
	}
}

func TestDescriptorRejectsMalformedCapabilityFacts(t *testing.T) {
	cases := []struct {
		name       string
		operation  api.Operation
		capability OperationCapability
	}{
		{name: "empty modes", operation: api.OperationGenerate, capability: OperationCapability{}},
		{name: "unknown mode", operation: api.OperationGenerate, capability: OperationCapability{Modes: []api.DeliveryMode{"poll"}}},
		{name: "duplicate mode", operation: api.OperationGenerate, capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary, api.ModeUnary}}},
		{name: "unknown feature", operation: api.OperationGenerate, capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Features: []api.Feature{"private"}}},
		{name: "duplicate feature", operation: api.OperationGenerate, capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Features: []api.Feature{api.FeatureUsage, api.FeatureUsage}}},
		{name: "unknown thinking", operation: api.OperationGenerate, capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []ReasoningCapability{{Thinking: "sometimes"}}}},
		{name: "duplicate thinking", operation: api.OperationGenerate, capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []ReasoningCapability{{Thinking: api.ThinkingEnabled}, {Thinking: api.ThinkingEnabled}}}},
		{name: "duplicate effort", operation: api.OperationGenerate, capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []ReasoningCapability{{Thinking: api.ThinkingEnabled, Efforts: []api.ReasoningEffort{api.ReasoningEffortHigh, api.ReasoningEffortHigh}}}}},
		{name: "disabled effort", operation: api.OperationGenerate, capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []ReasoningCapability{{Thinking: api.ThinkingDisabled, Efforts: []api.ReasoningEffort{api.ReasoningEffortHigh}}}}},
		{name: "reasoning on media", operation: api.OperationImageGeneration, capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []ReasoningCapability{{Thinking: api.ThinkingDisabled}}}},
		{name: "unknown operation", operation: "future_operation", capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}}},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			if _, err := NewDescriptor(DescriptorConfig{Kind: "service.test", Protocols: []string{"protocol.test"}, Models: []string{"model.test"}, Operations: map[api.Operation]OperationCapability{testCase.operation: testCase.capability}}); err == nil {
				t.Fatalf("malformed capability %q was accepted", testCase.name)
			}
		})
	}
}

func TestDescriptorRejectsMalformedIdentity(t *testing.T) {
	operation := map[api.Operation]OperationCapability{api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}}}
	for _, identifier := range []string{" service.test", "service.test ", "service test", "service!test", "Service.Bad", "service/bad", "service:bad", strings.Repeat("a", 129)} {
		if _, err := NewDescriptor(DescriptorConfig{Kind: identifier, Protocols: []string{"protocol.test"}, Models: []string{"model.test"}, Operations: operation}); err == nil {
			t.Fatalf("invalid service identity %q was accepted", identifier)
		}
	}
	for _, protocolID := range []string{" protocol.test", "protocol test", "protocol!test", "Protocol.test", "protocol/test", "protocol:bad"} {
		if _, err := NewDescriptor(DescriptorConfig{Kind: "service.test", Protocols: []string{protocolID}, Models: []string{"model.test"}, Operations: operation}); err == nil {
			t.Fatalf("invalid protocol identity %q was accepted", protocolID)
		}
	}
	for _, baseModel := range []string{"bad/model", " bad", "", strings.Repeat("a", 257)} {
		if _, err := NewDescriptor(DescriptorConfig{Kind: "service.test", Protocols: []string{"protocol.test"}, Models: []string{baseModel}, Operations: operation}); err == nil {
			t.Fatalf("invalid BaseModel %q was accepted by service allowlist", baseModel)
		}
	}
}
