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
	descriptor, err := NewDescriptor("protocol.test", map[api.Operation]OperationCapability{
		api.OperationGenerate: {Modes: modes, Features: features, Reasoning: reasoning},
	})
	if err != nil {
		t.Fatalf("construct descriptor: %v", err)
	}

	modes[0] = api.ModeAsync
	features[0] = api.FeatureUsage
	efforts[0] = api.ReasoningEffortLow
	reasoning[0].Thinking = api.ThinkingDisabled

	if !descriptor.SupportsOperation(api.OperationGenerate, api.ModeUnary) ||
		!descriptor.SupportsFeature(api.OperationGenerate, api.FeatureToolCalls) ||
		!descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingAdaptive, Effort: api.ReasoningEffortHigh}) {
		t.Fatal("descriptor retained caller-owned capability slices")
	}
	if descriptor.SupportsReasoning(api.OperationGenerate, &api.ReasoningConfig{Thinking: api.ThinkingAdaptive, Effort: api.ReasoningEffortLow}) {
		t.Fatal("descriptor accepted an undeclared reasoning effort")
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

func TestDescriptorRejectsMalformedIdentity(t *testing.T) {
	operation := map[api.Operation]OperationCapability{api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}}}
	tooLong := "protocol." + strings.Repeat("a", 121)
	for _, identifier := range []string{" protocol.test", "protocol.test ", "protocol test", "protocol!test", "Protocol.Bad", "protocol/bad", "protocol:bad", "protocol", tooLong} {
		if _, err := NewDescriptor(identifier, operation); err == nil {
			t.Fatalf("invalid protocol identity %q was accepted", identifier)
		}
	}
}
