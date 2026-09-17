package testkit

import (
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/protocol"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/service"
)

// ProtocolCapabilities returns an all-capabilities descriptor for deterministic
// tests. Production composition must use the descriptor of its bound adapter.
func ProtocolCapabilities() protocol.Descriptor {
	allFeatures, reasoning := fixtureCapabilities()
	operations := map[api.Operation]protocol.OperationCapability{
		api.OperationGenerate:           {Modes: []api.DeliveryMode{api.ModeUnary, api.ModeServerStream}, Features: allFeatures, Reasoning: reasoning},
		api.OperationRealtime:           {Modes: []api.DeliveryMode{api.ModeDuplex}, Features: allFeatures, Reasoning: reasoning},
		api.OperationImageGeneration:    fixtureProtocolMediaCapability(),
		api.OperationAudioGeneration:    fixtureProtocolMediaCapability(),
		api.OperationMusicGeneration:    fixtureProtocolMediaCapability(),
		api.OperationVideoGeneration:    fixtureProtocolMediaCapability(),
		api.OperationAudioTranscription: fixtureProtocolMediaCapability(),
	}
	descriptor, err := protocol.NewDescriptor("fixture.protocol", operations)
	if err != nil {
		panic(err)
	}
	return descriptor
}

// ServiceCapabilities returns an all-capabilities descriptor for the exact
// fixture models named by the test. It is not a production service
// registration and deliberately has no wildcard model support.
func ServiceCapabilities(models ...string) service.Descriptor {
	if len(models) == 0 {
		models = []string{
			"chatgpt-4o", "dall-e-3", "gpt-realtime-1.5", "first", "second",
			"model.text", "model.tools", "model.reasoning", "model.realtime",
			"model.image", "model.audio", "model.music", "model.video", "model.transcribe",
		}
	}
	allFeatures, reasoning := fixtureServiceCapabilities()
	operations := map[api.Operation]service.OperationCapability{
		api.OperationGenerate:           {Modes: []api.DeliveryMode{api.ModeUnary, api.ModeServerStream}, Features: allFeatures, Reasoning: reasoning},
		api.OperationRealtime:           {Modes: []api.DeliveryMode{api.ModeDuplex}, Features: allFeatures, Reasoning: reasoning},
		api.OperationImageGeneration:    fixtureServiceMediaCapability(),
		api.OperationAudioGeneration:    fixtureServiceMediaCapability(),
		api.OperationMusicGeneration:    fixtureServiceMediaCapability(),
		api.OperationVideoGeneration:    fixtureServiceMediaCapability(),
		api.OperationAudioTranscription: fixtureServiceMediaCapability(),
	}
	descriptor, err := service.NewDescriptor(service.DescriptorConfig{
		Kind:       "fixture.service",
		Protocols:  []string{"fixture.protocol"},
		Models:     models,
		Operations: operations,
	})
	if err != nil {
		panic(err)
	}
	return descriptor
}

func fixtureCapabilities() ([]api.Feature, []protocol.ReasoningCapability) {
	allFeatures := []api.Feature{api.FeatureToolCalls, api.FeatureStructured, api.FeaturePromptCache, api.FeatureUsage}
	allEfforts := []api.ReasoningEffort{
		api.ReasoningEffortMinimal, api.ReasoningEffortLow, api.ReasoningEffortMedium,
		api.ReasoningEffortHigh, api.ReasoningEffortXHigh, api.ReasoningEffortMax,
	}
	return allFeatures, []protocol.ReasoningCapability{
		{Thinking: api.ThinkingDisabled},
		{Thinking: api.ThinkingEnabled, Efforts: allEfforts},
		{Thinking: api.ThinkingAdaptive, Efforts: allEfforts},
	}
}

func fixtureServiceCapabilities() ([]api.Feature, []service.ReasoningCapability) {
	allFeatures := []api.Feature{api.FeatureToolCalls, api.FeatureStructured, api.FeaturePromptCache, api.FeatureUsage}
	allEfforts := []api.ReasoningEffort{
		api.ReasoningEffortMinimal, api.ReasoningEffortLow, api.ReasoningEffortMedium,
		api.ReasoningEffortHigh, api.ReasoningEffortXHigh, api.ReasoningEffortMax,
	}
	return allFeatures, []service.ReasoningCapability{
		{Thinking: api.ThinkingDisabled},
		{Thinking: api.ThinkingEnabled, Efforts: allEfforts},
		{Thinking: api.ThinkingAdaptive, Efforts: allEfforts},
	}
}

func fixtureProtocolMediaCapability() protocol.OperationCapability {
	return protocol.OperationCapability{
		Modes:    []api.DeliveryMode{api.ModeUnary, api.ModeServerStream, api.ModeAsync},
		Features: []api.Feature{api.FeaturePromptCache, api.FeatureUsage},
	}
}

func fixtureServiceMediaCapability() service.OperationCapability {
	return service.OperationCapability{
		Modes:    []api.DeliveryMode{api.ModeUnary, api.ModeServerStream, api.ModeAsync},
		Features: []api.Feature{api.FeaturePromptCache, api.FeatureUsage},
	}
}
