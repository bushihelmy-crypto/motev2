package protocol

import (
	"strings"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

func TestDescriptorFreezesProtocolCapabilities(t *testing.T) {
	modes := []api.DeliveryMode{api.ModeUnary}
	features := []api.Feature{api.FeatureToolCalls}
	efforts := []api.ReasoningEffort{api.ReasoningEffortHigh}
	reasoning := []ReasoningCapability{{Thinking: api.ThinkingAdaptive, Efforts: efforts}}
	operations := map[api.Operation]OperationCapability{
		api.OperationGenerate: {Modes: modes, Features: features, Reasoning: reasoning},
	}
	descriptor, err := NewDescriptor("protocol.test", operations)
	if err != nil {
		t.Fatalf("construct descriptor: %v", err)
	}

	modes[0] = api.ModeAsync
	features[0] = api.FeatureUsage
	efforts[0] = api.ReasoningEffortLow
	reasoning[0].Thinking = api.ThinkingDisabled
	delete(operations, api.OperationGenerate)
	operations[api.OperationRealtime] = OperationCapability{Modes: []api.DeliveryMode{api.ModeDuplex}}

	if !descriptor.SupportsOperation(api.OperationGenerate, api.ModeUnary) ||
		!descriptor.SupportsFeature(api.OperationGenerate, api.FeatureToolCalls) ||
		!descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingAdaptive, Effort: api.ReasoningEffortHigh}) {
		t.Fatal("descriptor retained caller-owned capability slices")
	}
	if descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingAdaptive, Effort: api.ReasoningEffortLow}) {
		t.Fatal("descriptor accepted an undeclared reasoning effort")
	}
	if descriptor.SupportsReasoning(api.OperationRealtime, &api.ReasoningConfig{Thinking: api.ThinkingAdaptive}) {
		t.Fatal("descriptor invented reasoning support for an undeclared operation")
	}
	if descriptor.SupportsOperation(api.OperationRealtime, api.ModeDuplex) {
		t.Fatal("descriptor retained caller-owned operations map")
	}
}

func TestDescriptorRejectsMissingProtocolIdentityOrOperations(t *testing.T) {
	operation := map[api.Operation]OperationCapability{api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}}}
	if _, err := NewDescriptor("", operation); err == nil {
		t.Fatal("empty protocol identity was accepted")
	}
	if _, err := NewDescriptor("protocol.test", nil); err == nil {
		t.Fatal("protocol without operations was accepted")
	}
}

func TestDescriptorRejectsMalformedCapabilityFacts(t *testing.T) {
	cases := []struct {
		name       string
		capability OperationCapability
	}{
		{name: "empty modes", capability: OperationCapability{}},
		{name: "unknown mode", capability: OperationCapability{Modes: []api.DeliveryMode{"poll"}}},
		{name: "duplicate mode", capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary, api.ModeUnary}}},
		{name: "unknown feature", capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Features: []api.Feature{"private"}}},
		{name: "duplicate feature", capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Features: []api.Feature{api.FeatureUsage, api.FeatureUsage}}},
		{name: "unknown thinking", capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []ReasoningCapability{{Thinking: "sometimes"}}}},
		{name: "duplicate thinking", capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []ReasoningCapability{{Thinking: api.ThinkingEnabled}, {Thinking: api.ThinkingEnabled}}}},
		{name: "duplicate effort", capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []ReasoningCapability{{Thinking: api.ThinkingEnabled, Efforts: []api.ReasoningEffort{api.ReasoningEffortHigh, api.ReasoningEffortHigh}}}}},
		{name: "unknown effort", capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []ReasoningCapability{{Thinking: api.ThinkingEnabled, Efforts: []api.ReasoningEffort{"huge"}}}}},
		{name: "disabled effort", capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []ReasoningCapability{{Thinking: api.ThinkingDisabled, Efforts: []api.ReasoningEffort{api.ReasoningEffortHigh}}}}},
		{name: "reasoning on media", capability: OperationCapability{Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []ReasoningCapability{{Thinking: api.ThinkingDisabled}}}},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			operation := api.OperationGenerate
			if testCase.name == "reasoning on media" {
				operation = api.OperationImageGeneration
			}
			if _, err := NewDescriptor("protocol.test", map[api.Operation]OperationCapability{operation: testCase.capability}); err == nil {
				t.Fatalf("malformed capability %q was accepted", testCase.name)
			}
		})
	}
}

func TestDescriptorRejectsUnknownOperation(t *testing.T) {
	if _, err := NewDescriptor("protocol.test", map[api.Operation]OperationCapability{
		"future_operation": {Modes: []api.DeliveryMode{api.ModeUnary}},
	}); err == nil {
		t.Fatal("unknown protocol operation was accepted")
	}
}

func TestDescriptorRejectsMalformedIdentity(t *testing.T) {
	operation := map[api.Operation]OperationCapability{api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}}}
	tooLong := "protocol." + strings.Repeat("a", 121)
	for _, identifier := range []string{" protocol.test", "protocol.test ", "protocol test", "protocol!test", "Protocol.Bad", "protocol/bad", "protocol:bad", "protocol", tooLong} {
		if _, err := NewDescriptor(identifier, operation); err == nil {
			t.Fatalf("invalid protocol identity %q was accepted", identifier)
		}
	}
}

func TestDescriptorCapabilityQueriesAreExactAndFailClosed(t *testing.T) {
	descriptor, err := NewDescriptor("openai.responses.v1", map[api.Operation]OperationCapability{
		api.OperationGenerate: {
			Modes:    []api.DeliveryMode{api.ModeUnary, api.ModeServerStream},
			Features: []api.Feature{api.FeatureToolCalls, api.FeatureStructured},
			Reasoning: []ReasoningCapability{
				{Thinking: api.ThinkingDisabled},
				{Thinking: api.ThinkingEnabled, Efforts: []api.ReasoningEffort{api.ReasoningEffortLow, api.ReasoningEffortHigh}},
				{Thinking: api.ThinkingAdaptive},
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
		{name: "known unary mode", got: descriptor.SupportsOperation(api.OperationGenerate, api.ModeUnary), want: true},
		{name: "known stream mode", got: descriptor.SupportsOperation(api.OperationGenerate, api.ModeServerStream), want: true},
		{name: "unknown mode", got: descriptor.SupportsOperation(api.OperationGenerate, api.ModeDuplex), want: false},
		{name: "unknown operation", got: descriptor.SupportsOperation(api.OperationRealtime, api.ModeUnary), want: false},
		{name: "tool feature", got: descriptor.SupportsFeature(api.OperationGenerate, api.FeatureToolCalls), want: true},
		{name: "feature on unknown operation", got: descriptor.SupportsFeature(api.OperationRealtime, api.FeatureToolCalls), want: false},
		{name: "disabled reasoning", got: descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingDisabled}), want: true},
		{name: "enabled mode-only", got: descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingEnabled}), want: true},
		{name: "enabled low", got: descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingEnabled, Effort: api.ReasoningEffortLow}), want: true},
		{name: "enabled high", got: descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingEnabled, Effort: api.ReasoningEffortHigh}), want: true},
		{name: "enabled medium absent", got: descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingEnabled, Effort: api.ReasoningEffortMedium}), want: false},
		{name: "adaptive mode-only", got: descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingAdaptive}), want: true},
		{name: "nil reasoning", got: descriptor.SupportsReasoning(api.OperationGenerate, nil), want: true},
		{name: "reasoning unknown operation", got: descriptor.SupportsReasoning(api.OperationRealtime, &api.ReasoningConfig{Thinking: api.ThinkingAdaptive}), want: false},
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
	for _, identifier := range []string{"a.b", "a." + strings.Repeat("z", 126)} {
		if _, err := NewDescriptor(identifier, operation); err != nil {
			t.Errorf("valid protocol identity %q rejected: %v", identifier, err)
		}
	}
	for _, identifier := range []string{"a." + strings.Repeat("z", 127), "a.b." + strings.Repeat("z", 125)} {
		if _, err := NewDescriptor(identifier, operation); err == nil {
			t.Errorf("overlong protocol identity %q accepted", identifier)
		}
	}
}
