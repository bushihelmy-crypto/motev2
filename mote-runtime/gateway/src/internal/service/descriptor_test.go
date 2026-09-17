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
	operations := map[api.Operation]OperationCapability{
		api.OperationGenerate: {Modes: modes, Features: features, Reasoning: reasoning},
	}
	descriptor, err := NewDescriptor(DescriptorConfig{Kind: "service.test", Protocols: protocols, Models: models, Operations: operations})
	if err != nil {
		t.Fatalf("construct descriptor: %v", err)
	}

	modes[0] = api.ModeAsync
	features[0] = api.FeatureUsage
	efforts[0] = api.ReasoningEffortHigh
	reasoning[0].Thinking = api.ThinkingDisabled
	protocols[0] = "protocol.changed"
	models[0] = "model.changed"
	delete(operations, api.OperationGenerate)
	operations[api.OperationRealtime] = OperationCapability{Modes: []api.DeliveryMode{api.ModeDuplex}}

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
	if descriptor.SupportsReasoning(api.OperationRealtime, &api.ReasoningConfig{Thinking: api.ThinkingEnabled}) {
		t.Fatal("descriptor invented reasoning support for an undeclared operation")
	}
	if descriptor.SupportsOperation(api.OperationRealtime, api.ModeDuplex) {
		t.Fatal("descriptor retained caller-owned operations map")
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
	if _, err := NewDescriptor(DescriptorConfig{Kind: "service.test", Protocols: []string{"protocol.test"}, Models: []string{"model.test", "model.test"}, Operations: operation}); err == nil {
		t.Fatal("service with duplicate exact models was accepted")
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
		{name: "unknown effort", operation: api.OperationGenerate, capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []ReasoningCapability{{Thinking: api.ThinkingEnabled, Efforts: []api.ReasoningEffort{"huge"}}}}},
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

func TestDescriptorCapabilityQueriesAreExactAndFailClosed(t *testing.T) {
	descriptor, err := NewDescriptor(DescriptorConfig{
		Kind:      "service.exact",
		Protocols: []string{"openai.responses.v1"},
		Models:    []string{"model.exact"},
		Operations: map[api.Operation]OperationCapability{
			api.OperationGenerate: {
				Modes:    []api.DeliveryMode{api.ModeUnary, api.ModeServerStream},
				Features: []api.Feature{api.FeatureToolCalls, api.FeatureStructured},
				Reasoning: []ReasoningCapability{
					{Thinking: api.ThinkingDisabled},
					{Thinking: api.ThinkingEnabled, Efforts: []api.ReasoningEffort{api.ReasoningEffortLow, api.ReasoningEffortHigh}},
					{Thinking: api.ThinkingAdaptive},
				},
			},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	for _, testCase := range []struct {
		name string
		got  bool
		want bool
	}{
		{name: "protocol exact", got: descriptor.SupportsProtocol("openai.responses.v1"), want: true},
		{name: "protocol case variant", got: descriptor.SupportsProtocol("OpenAI.responses.v1"), want: false},
		{name: "protocol unknown", got: descriptor.SupportsProtocol("openai.chat.v1"), want: false},
		{name: "model exact", got: descriptor.SupportsModel("model.exact"), want: true},
		{name: "model case variant", got: descriptor.SupportsModel("Model.exact"), want: false},
		{name: "model alias", got: descriptor.SupportsModel("provider/model.exact"), want: false},
		{name: "known unary mode", got: descriptor.SupportsOperation(api.OperationGenerate, api.ModeUnary), want: true},
		{name: "unknown mode", got: descriptor.SupportsOperation(api.OperationGenerate, api.ModeDuplex), want: false},
		{name: "unknown operation", got: descriptor.SupportsOperation(api.OperationRealtime, api.ModeUnary), want: false},
		{name: "tool feature", got: descriptor.SupportsFeature(api.OperationGenerate, api.FeatureToolCalls), want: true},
		{name: "nil reasoning", got: descriptor.SupportsReasoning(api.OperationGenerate, nil), want: true},
		{name: "enabled mode-only", got: descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingEnabled}), want: true},
		{name: "enabled low", got: descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingEnabled, Effort: api.ReasoningEffortLow}), want: true},
		{name: "enabled medium absent", got: descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingEnabled, Effort: api.ReasoningEffortMedium}), want: false},
		{name: "adaptive mode-only", got: descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingAdaptive}), want: true},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			if testCase.got != testCase.want {
				t.Fatalf("got %v, want %v", testCase.got, testCase.want)
			}
		})
	}
}

func TestDescriptorAcceptsIdentityLengthBoundaries(t *testing.T) {
	operation := map[api.Operation]OperationCapability{api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}}}
	for _, kind := range []string{"a", "a" + strings.Repeat("z", 127)} {
		if _, err := NewDescriptor(DescriptorConfig{Kind: kind, Protocols: []string{"a.b"}, Models: []string{"model"}, Operations: operation}); err != nil {
			t.Errorf("valid service identity %q rejected: %v", kind, err)
		}
	}
	for _, kind := range []string{"a" + strings.Repeat("z", 128), "A" + strings.Repeat("z", 127)} {
		if _, err := NewDescriptor(DescriptorConfig{Kind: kind, Protocols: []string{"a.b"}, Models: []string{"model"}, Operations: operation}); err == nil {
			t.Errorf("invalid service identity %q accepted", kind)
		}
	}
}
