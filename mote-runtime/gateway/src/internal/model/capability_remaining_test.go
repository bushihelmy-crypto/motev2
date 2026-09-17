package model

import (
	"errors"
	"strings"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

func TestReasoningErrorsDescribeTheExactPreference(t *testing.T) {
	requestError := (&ReasoningRequestError{BaseModel: "model", Reason: "ambiguous"}).Error()
	if requestError != `invalid reasoning request for model "model": ambiguous` {
		t.Fatalf("unexpected reasoning request error: %q", requestError)
	}
	unsupported := (&ReasoningUnsupportedError{
		BaseModel: "model", Thinking: api.ThinkingAdaptive, Effort: api.ReasoningEffortHigh, Reason: "not declared",
	}).Error()
	if !strings.Contains(unsupported, `thinking="adaptive"`) || !strings.Contains(unsupported, `effort="high"`) {
		t.Fatalf("reasoning preference was lost: %q", unsupported)
	}
}

func TestCapabilitiesWithoutEmbeddingFactsExposeNoDimensionPolicy(t *testing.T) {
	catalog, err := newCatalog([]Config{
		testModelConfig("generate-only"),
		embeddingModelConfig("embedding-without-width", &EmbeddingPolicy{}),
	})
	if err != nil {
		t.Fatal(err)
	}
	definition, _ := catalog.Lookup("generate-only")
	capability, _ := definition.Capability(api.OperationGenerate)
	requested := int64(256)
	if capability.ResolveEmbeddingDimensions(&requested) != nil {
		t.Fatal("non-embedding model resolved embedding dimensions")
	}
	if value, known := capability.DefaultEmbeddingDimensions(); known || value != 0 {
		t.Fatalf("non-embedding model invented a default width: %d %v", value, known)
	}

	definition, _ = catalog.Lookup("embedding-without-width")
	capability, _ = definition.Capability(api.OperationEmbedding)
	if value, known := capability.DefaultEmbeddingDimensions(); known || value != 0 {
		t.Fatalf("unspecified embedding width became authoritative: %d %v", value, known)
	}
}

func TestReasoningPolicyMayDeclareModesWithoutChoosingADefault(t *testing.T) {
	config := testModelConfig("upstream-default-reasoning")
	config.Capability.Reasoning = &ReasoningPolicy{ThinkingModes: []ThinkingModePolicy{{
		Thinking: api.ThinkingEnabled,
		Efforts:  []api.ReasoningEffort{api.ReasoningEffortHigh},
	}}}
	catalog, err := newCatalog([]Config{config})
	if err != nil {
		t.Fatal(err)
	}
	definition, _ := catalog.Lookup(config.BaseModel)
	capability, _ := definition.Capability(api.OperationGenerate)
	resolved, err := capability.ResolveReasoning(nil)
	if err != nil || resolved != nil {
		t.Fatalf("omitted reasoning should leave the upstream default untouched: %+v %v", resolved, err)
	}

	_, err = capability.ResolveReasoning(&api.ReasoningConfig{Effort: api.ReasoningEffortLow})
	var unsupported *ReasoningUnsupportedError
	if !errors.As(err, &unsupported) || unsupported.Effort != api.ReasoningEffortLow {
		t.Fatalf("effort outside every mode returned the wrong error: %T %v", err, err)
	}
}

func TestGenerationPolicyMayOmitOutputTokenSupport(t *testing.T) {
	config := testModelConfig("temperature-only")
	config.Capability.Generation = &GenerationPolicy{Temperature: &NumericParameter[float64]{}}
	catalog, err := newCatalog([]Config{config})
	if err != nil {
		t.Fatal(err)
	}
	definition, _ := catalog.Lookup(config.BaseModel)
	capability, _ := definition.Capability(api.OperationGenerate)
	requested := int64(128)
	if resolved := capability.ResolveGenerationParameters(api.GenerationParameters{MaxOutputTokens: &requested}); resolved.MaxOutputTokens != nil {
		t.Fatalf("unsupported output-token control leaked through: %+v", resolved)
	}
}

func TestCatalogRejectsRemainingMalformedPolicies(t *testing.T) {
	seedBelowMinimum := testModelConfig("seed-below-minimum")
	seedBelowMinimum.Capability.Generation.Seed = &NumericParameter[int64]{Minimum: pointer(int64(5)), Default: pointer(int64(1))}

	invalidDefaultThinking := testModelConfig("invalid-default-thinking")
	invalidThinking := api.ThinkingMode("sometimes")
	invalidDefaultThinking.Capability.Reasoning = &ReasoningPolicy{
		ThinkingModes:   []ThinkingModePolicy{{Thinking: api.ThinkingEnabled}},
		DefaultThinking: &invalidThinking,
	}

	invalidEffort := testModelConfig("invalid-effort")
	invalidEffort.Capability.Reasoning = &ReasoningPolicy{ThinkingModes: []ThinkingModePolicy{{
		Thinking: api.ThinkingEnabled, Efforts: []api.ReasoningEffort{"huge"},
	}}}

	disabledDefaultEffort := testModelConfig("disabled-default-effort")
	disabledDefaultEffort.Capability.Reasoning = &ReasoningPolicy{ThinkingModes: []ThinkingModePolicy{{
		Thinking: api.ThinkingDisabled, DefaultEffort: pointer(api.ReasoningEffortHigh),
	}}}

	reversedDimensions := embeddingModelConfig("reversed-dimensions", &EmbeddingPolicy{Dimensions: &NumericParameter[int64]{
		Minimum: pointer(int64(1024)), Maximum: pointer(int64(128)),
	}})

	missingRequiredOutput := testModelConfig("audio-without-audio-output")
	missingRequiredOutput.Capability.Operation = api.OperationAudioGeneration
	missingRequiredOutput.Capability.OutputModalities = []api.Modality{api.ModalityText}

	tests := []struct {
		name   string
		config Config
		field  string
	}{
		{name: "seed default below minimum", config: seedBelowMinimum, field: "capability.generation.seed.default"},
		{name: "invalid default thinking", config: invalidDefaultThinking, field: "capability.reasoning.default_thinking"},
		{name: "invalid reasoning effort", config: invalidEffort, field: "capability.reasoning.thinking_modes[0].efforts"},
		{name: "disabled default effort", config: disabledDefaultEffort, field: "capability.reasoning.thinking_modes[0].default_effort"},
		{name: "reversed embedding dimensions", config: reversedDimensions, field: "capability.embedding.dimensions.bounds"},
		{name: "missing required output", config: missingRequiredOutput, field: "capability"},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := newCatalog([]Config{testCase.config})
			var configErrorValue *ConfigError
			if !errors.As(err, &configErrorValue) || configErrorValue.Field != testCase.field {
				t.Fatalf("expected %q config error, got %T %v", testCase.field, err, err)
			}
		})
	}
}

func TestDefinitionCapabilityRequiresItsExactOperation(t *testing.T) {
	catalog, err := newCatalog([]Config{testModelConfig("one-operation")})
	if err != nil {
		t.Fatal(err)
	}
	definition, _ := catalog.Lookup("one-operation")
	if capability, ok := definition.Capability(api.OperationEmbedding); ok || capability.BaseModel() != "" {
		t.Fatal("definition exposed a capability for another operation")
	}
}

func TestThinkingModeComparatorHandlesEqualValues(t *testing.T) {
	mode := ThinkingModePolicy{Thinking: api.ThinkingEnabled}
	if compareThinkingModes(mode, mode) != 0 {
		t.Fatal("equal thinking modes did not compare equal")
	}
}
