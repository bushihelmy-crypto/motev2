package model

import (
	"bytes"
	"encoding/json"
	"errors"
	"math"
	"strings"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

func TestCatalogFreezesCompleteSourceConfigs(t *testing.T) {
	configured := testModelConfig("configured-model")
	configured.Lifecycle = LifecycleDeprecated
	configured.TokenLimits = TokenLimits{
		ContextWindowTokens: 4096,
		MaxInputTokens:      3500,
		MinOutputTokens:     2,
		MaxOutputTokens:     1024,
	}
	configured.Capability = CapabilityConfig{
		Operation:        api.OperationGenerate,
		InputModalities:  []api.Modality{api.ModalityText, api.ModalityImage},
		OutputModalities: []api.Modality{api.ModalityText},
		Generation:       &GenerationPolicy{MaxOutputTokens: &OutputTokenParameter{}},
	}
	configs := []Config{testModelConfig("default-model"), configured}

	catalog, err := newCatalog(configs)
	if err != nil {
		t.Fatalf("construct catalog: %v", err)
	}

	configs[0].Capability.InputModalities[0] = api.ModalityAudio
	configs[1].Lifecycle = LifecycleRetired
	configs[1].TokenLimits.MaxOutputTokens = 1
	configs[1].Capability.InputModalities[0] = api.ModalityAudio
	configs[1].Capability.Generation.MaxOutputTokens = nil

	inherited, err := catalog.Lookup("default-model")
	if err != nil {
		t.Fatalf("lookup inherited model: %v", err)
	}
	inheritedCapability, ok := inherited.Capability(api.OperationGenerate)
	if !ok || !inheritedCapability.SupportsInputModality(api.ModalityText) || inheritedCapability.SupportsInputModality(api.ModalityAudio) {
		t.Fatal("default model did not retain its frozen capability")
	}

	definition, err := catalog.Lookup("configured-model")
	if err != nil {
		t.Fatalf("lookup configured model: %v", err)
	}
	if definition.BaseModel() != "configured-model" || definition.Lifecycle() != LifecycleDeprecated {
		t.Fatalf("source model envelope was not retained: base_model=%q lifecycle=%q", definition.BaseModel(), definition.Lifecycle())
	}
	limits := definition.TokenLimits()
	if limits.ContextWindowTokens != 4096 || limits.MaxInputTokens != 3500 || limits.MinOutputTokens != 2 || limits.MaxOutputTokens != 1024 {
		t.Fatalf("source token limits were not retained: %+v", limits)
	}
	capability, ok := definition.Capability(api.OperationGenerate)
	if !ok || !capability.SupportsInputModality(api.ModalityText) || !capability.SupportsInputModality(api.ModalityImage) {
		t.Fatal("source capability was not retained")
	}
	if capability.SupportsFeature(api.FeatureUsage) {
		t.Fatal("service-owned feature leaked into the model capability")
	}
	parameters := capability.ResolveGenerationParameters(api.GenerationParameters{})
	if parameters.MaxOutputTokens == nil || *parameters.MaxOutputTokens != 1024 {
		t.Fatalf("central output-token policy was not clamped to the override limit: %+v", parameters)
	}

	_, err = catalog.Lookup("configured-model ")
	var notFound *ModelNotFoundError
	if !errors.As(err, &notFound) || notFound.BaseModel != "configured-model " {
		t.Fatalf("lookup must use the exact BaseModel without aliases: %T %v", err, err)
	}
}

func TestDefinitionResolvesOmittedOperationAndRejectsExplicitMismatch(t *testing.T) {
	catalog, err := newCatalog([]Config{testModelConfig("model-a")})
	if err != nil {
		t.Fatalf("construct catalog: %v", err)
	}
	definition, err := catalog.Lookup("model-a")
	if err != nil {
		t.Fatalf("lookup definition: %v", err)
	}

	defaulted, err := definition.ResolveOperation(nil)
	if err != nil || defaulted != api.OperationGenerate {
		t.Fatalf("omitted operation did not use the model default: %q %v", defaulted, err)
	}
	explicitOperation := api.OperationGenerate
	explicit, err := definition.ResolveOperation(&explicitOperation)
	if err != nil || explicit != api.OperationGenerate {
		t.Fatalf("matching operation was rejected: %q %v", explicit, err)
	}
	mismatchedOperation := api.OperationEmbedding
	_, err = definition.ResolveOperation(&mismatchedOperation)
	var mismatch *OperationMismatchError
	if !errors.As(err, &mismatch) || mismatch.BaseModel != "model-a" || mismatch.Available != api.OperationGenerate || mismatch.Requested != api.OperationEmbedding {
		t.Fatalf("operation mismatch was not typed: %T %v", err, err)
	}
}

func TestCapabilityResolvesDefaultsRequestsFilteringAndKnownBounds(t *testing.T) {
	catalog, err := newCatalog([]Config{testModelConfig("model-a")})
	if err != nil {
		t.Fatalf("construct catalog: %v", err)
	}
	definition, _ := catalog.Lookup("model-a")
	capability, _ := definition.Capability(api.OperationGenerate)

	requested := api.GenerationParameters{
		Temperature:     pointer(0.8),
		TopP:            pointer(0.9),
		MaxOutputTokens: pointer(int64(128)),
		Stop:            []string{"DONE"},
		Seed:            pointer(int64(7)),
	}
	resolved := capability.ResolveGenerationParameters(requested)
	if resolved.MaxOutputTokens == nil || *resolved.MaxOutputTokens != 128 || len(resolved.Stop) != 1 || resolved.Stop[0] != "DONE" {
		t.Fatalf("supported request values were not retained: %+v", resolved)
	}
	if resolved.Temperature != nil || resolved.TopP != nil || resolved.Seed != nil {
		t.Fatalf("known unsupported parameters were not filtered: %+v", resolved)
	}
	*requested.MaxOutputTokens = 1
	requested.Stop[0] = "MUTATED"
	if *resolved.MaxOutputTokens != 128 || resolved.Stop[0] != "DONE" {
		t.Fatal("resolved parameters retain mutable request state")
	}

	defaults := capability.ResolveGenerationParameters(api.GenerationParameters{})
	if defaults.MaxOutputTokens == nil || *defaults.MaxOutputTokens != 2048 || len(defaults.Stop) != 1 || defaults.Stop[0] != "END" {
		t.Fatalf("model defaults were not applied: %+v", defaults)
	}
	resolved.Stop[0] = "MUTATED-RESULT"
	again := capability.ResolveGenerationParameters(api.GenerationParameters{})
	if again.Stop[0] != "END" {
		t.Fatal("one resolution mutated the immutable model default")
	}
	cleared := capability.ResolveGenerationParameters(api.GenerationParameters{Stop: []string{}})
	if len(cleared.Stop) != 0 {
		t.Fatalf("an explicit empty supported value did not clear the default: %+v", cleared)
	}

	allSupported := testModelConfig("all-supported")
	allSupported.Capability.Generation = &GenerationPolicy{
		Temperature:     &NumericParameter[float64]{Minimum: pointer(0.0), Maximum: pointer(2.0)},
		TopP:            &NumericParameter[float64]{Minimum: pointer(0.000001), Maximum: pointer(1.0)},
		MaxOutputTokens: &OutputTokenParameter{},
		Stop:            &StopParameter{},
		Seed:            &NumericParameter[int64]{Minimum: pointer(int64(0)), Maximum: pointer(int64(100))},
	}
	allCatalog, err := newCatalog([]Config{allSupported})
	if err != nil {
		t.Fatalf("construct all-parameter catalog: %v", err)
	}
	allDefinition, _ := allCatalog.Lookup("all-supported")
	allCapability, _ := allDefinition.Capability(api.OperationGenerate)
	all := allCapability.ResolveGenerationParameters(api.GenerationParameters{
		Temperature: pointer(0.6), TopP: pointer(0.7), MaxOutputTokens: pointer(int64(64)),
		Stop: []string{"ALL"}, Seed: pointer(int64(11)),
	})
	if all.Temperature == nil || *all.Temperature != 0.6 || all.TopP == nil || *all.TopP != 0.7 ||
		all.MaxOutputTokens == nil || *all.MaxOutputTokens != 64 || all.Seed == nil || *all.Seed != 11 {
		t.Fatalf("supported request parameters were not retained: %+v", all)
	}
	empty := allCapability.ResolveGenerationParameters(api.GenerationParameters{})
	if empty.MaxOutputTokens == nil || *empty.MaxOutputTokens != 2048 || empty.Stop != nil {
		t.Fatalf("central output-token policy was not applied: %+v", empty)
	}
}

func TestCapabilityResolvesReasoningModesAndEfforts(t *testing.T) {
	config := reasoningModelConfig("reasoning-model")
	catalog, err := newCatalog([]Config{config})
	if err != nil {
		t.Fatalf("construct reasoning catalog: %v", err)
	}
	definition, _ := catalog.Lookup("reasoning-model")
	capability, ok := definition.Capability(api.OperationGenerate)
	if !ok {
		t.Fatal("reasoning model capability was not retained")
	}

	defaults, err := capability.ResolveReasoning(nil)
	if err != nil || defaults == nil || defaults.Thinking != api.ThinkingAdaptive || defaults.Effort != api.ReasoningEffortMedium {
		t.Fatalf("catalog reasoning defaults were not applied: %+v %v", defaults, err)
	}
	explicit, err := capability.ResolveReasoning(&api.ReasoningConfig{
		Thinking: api.ThinkingAdaptive,
		Effort:   api.ReasoningEffortHigh,
	})
	if err != nil || explicit == nil || explicit.Thinking != api.ThinkingAdaptive || explicit.Effort != api.ReasoningEffortHigh {
		t.Fatalf("explicit adaptive effort was not retained: %+v %v", explicit, err)
	}
	disabled, err := capability.ResolveReasoning(&api.ReasoningConfig{Thinking: api.ThinkingDisabled})
	if err != nil || disabled == nil || disabled.Thinking != api.ThinkingDisabled || disabled.Effort != "" {
		t.Fatalf("disabled thinking was not resolved: %+v %v", disabled, err)
	}

	_, err = capability.ResolveReasoning(&api.ReasoningConfig{
		Thinking: api.ThinkingDisabled,
		Effort:   api.ReasoningEffortHigh,
	})
	var requestError *ReasoningRequestError
	if !errors.As(err, &requestError) {
		t.Fatalf("disabled plus effort did not produce a typed request error: %T %v", err, err)
	}
	_, err = capability.ResolveReasoning(&api.ReasoningConfig{
		Thinking: api.ThinkingAdaptive,
		Effort:   api.ReasoningEffortMax,
	})
	var unsupported *ReasoningUnsupportedError
	if !errors.As(err, &unsupported) {
		t.Fatalf("unsupported effort did not produce a typed capability error: %T %v", err, err)
	}

	withoutThinking, err := capability.ResolveReasoning(&api.ReasoningConfig{Effort: api.ReasoningEffortLow})
	if err != nil || withoutThinking == nil || withoutThinking.Thinking != api.ThinkingAdaptive || withoutThinking.Effort != api.ReasoningEffortLow {
		t.Fatalf("effort-only request did not use the catalog default mode: %+v %v", withoutThinking, err)
	}
}

func TestCapabilityInfersTheOnlyThinkingModeForEffortOnlyRequest(t *testing.T) {
	config := testModelConfig("single-mode-reasoning")
	config.Capability.Reasoning = &ReasoningPolicy{
		ThinkingModes: []ThinkingModePolicy{{
			Thinking: api.ThinkingAdaptive,
			Efforts:  []api.ReasoningEffort{api.ReasoningEffortLow},
		}},
	}
	catalog, err := newCatalog([]Config{config})
	if err != nil {
		t.Fatalf("construct catalog: %v", err)
	}
	definition, _ := catalog.Lookup(config.BaseModel)
	capability, _ := definition.Capability(api.OperationGenerate)
	resolved, err := capability.ResolveReasoning(&api.ReasoningConfig{Effort: api.ReasoningEffortLow})
	if err != nil || resolved == nil || resolved.Thinking != api.ThinkingAdaptive || resolved.Effort != api.ReasoningEffortLow {
		t.Fatalf("unique thinking mode was not inferred: %+v %v", resolved, err)
	}
}

func TestCapabilityRejectsAmbiguousEffortOnlyRequestAsInvalid(t *testing.T) {
	config := testModelConfig("ambiguous-reasoning")
	config.Capability.Reasoning = &ReasoningPolicy{ThinkingModes: []ThinkingModePolicy{
		{Thinking: api.ThinkingEnabled, Efforts: []api.ReasoningEffort{api.ReasoningEffortLow}},
		{Thinking: api.ThinkingAdaptive, Efforts: []api.ReasoningEffort{api.ReasoningEffortLow}},
	}}
	catalog, err := newCatalog([]Config{config})
	if err != nil {
		t.Fatalf("construct catalog: %v", err)
	}
	definition, _ := catalog.Lookup(config.BaseModel)
	capability, _ := definition.Capability(api.OperationGenerate)
	_, err = capability.ResolveReasoning(&api.ReasoningConfig{Effort: api.ReasoningEffortLow})
	var requestError *ReasoningRequestError
	if !errors.As(err, &requestError) {
		t.Fatalf("ambiguous effort-only request returned the wrong error: %T %v", err, err)
	}
}

func TestCapabilityRejectsExplicitReasoningWhenUndeclared(t *testing.T) {
	catalog, err := newCatalog([]Config{testModelConfig("ordinary-model")})
	if err != nil {
		t.Fatalf("construct catalog: %v", err)
	}
	definition, _ := catalog.Lookup("ordinary-model")
	capability, _ := definition.Capability(api.OperationGenerate)
	if resolved, resolveErr := capability.ResolveReasoning(nil); resolveErr != nil || resolved != nil {
		t.Fatalf("omitted reasoning changed an ordinary model: %+v %v", resolved, resolveErr)
	}
	_, err = capability.ResolveReasoning(&api.ReasoningConfig{Thinking: api.ThinkingEnabled})
	var unsupported *ReasoningUnsupportedError
	if !errors.As(err, &unsupported) {
		t.Fatalf("undeclared reasoning was not rejected with a typed error: %T %v", err, err)
	}
}

func TestReasoningPolicyIsFrozenAndProviderFieldsStayOut(t *testing.T) {
	config := reasoningModelConfig("frozen-reasoning")
	defaultThinking := *config.Capability.Reasoning.DefaultThinking
	defaultEffort := *config.Capability.Reasoning.ThinkingModes[2].DefaultEffort
	catalog, err := newCatalog([]Config{config})
	if err != nil {
		t.Fatalf("construct reasoning catalog: %v", err)
	}
	config.Capability.Reasoning.DefaultThinking = pointer(api.ThinkingDisabled)
	config.Capability.Reasoning.ThinkingModes[2].Efforts[0] = api.ReasoningEffortMax
	config.Capability.Reasoning.ThinkingModes[2].DefaultEffort = pointer(api.ReasoningEffortLow)
	definition, _ := catalog.Lookup("frozen-reasoning")
	capability, _ := definition.Capability(api.OperationGenerate)
	resolved, err := capability.ResolveReasoning(nil)
	if err != nil || resolved == nil || resolved.Thinking != defaultThinking || resolved.Effort != defaultEffort {
		t.Fatalf("reasoning policy retained caller mutation: %+v %v", resolved, err)
	}

	for _, data := range []string{
		`{"schema_version":3,"models":[{"base_model":"m","lifecycle":"active","capability":{"operation":"generate","input_modalities":["text"],"output_modalities":["text"],"reasoning":{"thinking_modes":[{"thinking":"enabled","budget_tokens":1}]}}}]}`,
	} {
		if _, err := loadCatalogTestData([]byte(data)); err == nil {
			t.Fatal("provider-specific reasoning field was accepted by the catalog decoder")
		}
	}
}

func TestCapabilityClampsOnlyKnownNumericBoundaries(t *testing.T) {
	config := testModelConfig("bounded-model")
	config.Capability.Generation = &GenerationPolicy{
		Temperature:     &NumericParameter[float64]{Minimum: pointer(0.0), Maximum: pointer(2.0)},
		TopP:            &NumericParameter[float64]{Maximum: pointer(1.0)},
		MaxOutputTokens: &OutputTokenParameter{},
		Seed:            &NumericParameter[int64]{Minimum: pointer(int64(0))},
	}
	catalog, err := newCatalog([]Config{config})
	if err != nil {
		t.Fatalf("construct catalog: %v", err)
	}
	definition, _ := catalog.Lookup("bounded-model")
	capability, _ := definition.Capability(api.OperationGenerate)
	resolved := capability.ResolveGenerationParameters(api.GenerationParameters{
		Temperature:     pointer(-1.0),
		TopP:            pointer(3.0),
		MaxOutputTokens: pointer(int64(9999)),
		Seed:            pointer(int64(-1)),
	})
	if *resolved.Temperature != 0 || *resolved.TopP != 1 || *resolved.MaxOutputTokens != 2048 || *resolved.Seed != 0 {
		t.Fatalf("values were not mapped to known nearest boundaries: %+v", resolved)
	}

	unknownBounds := testModelConfig("unknown-bounds")
	unknownBounds.TokenLimits.MinOutputTokens = 0
	unknownBounds.TokenLimits.MaxOutputTokens = 0
	unknownBounds.Capability.Generation = &GenerationPolicy{
		Temperature:     &NumericParameter[float64]{},
		MaxOutputTokens: &OutputTokenParameter{},
	}
	unknownCatalog, err := newCatalog([]Config{unknownBounds})
	if err != nil {
		t.Fatalf("known support with unknown bounds must be valid: %v", err)
	}
	unknownDefinition, _ := unknownCatalog.Lookup("unknown-bounds")
	unknownCapability, _ := unknownDefinition.Capability(api.OperationGenerate)
	requestedTemperature := 123.5
	requestedOutput := int64(999999)
	unknown := unknownCapability.ResolveGenerationParameters(api.GenerationParameters{
		Temperature: &requestedTemperature, MaxOutputTokens: &requestedOutput,
	})
	if unknown.Temperature == nil || *unknown.Temperature != requestedTemperature ||
		unknown.MaxOutputTokens == nil || *unknown.MaxOutputTokens != requestedOutput {
		t.Fatalf("unknown bounds were treated as unsupported or invented: %+v", unknown)
	}
}

func TestEmbeddingCapabilityIsTypedAndIndependentFromGeneration(t *testing.T) {
	fixed := embeddingModelConfig("fixed-embedding", &EmbeddingPolicy{FixedDimensions: pointer(int64(768))})
	adjustable := embeddingModelConfig("adjustable-embedding", &EmbeddingPolicy{
		Dimensions: &NumericParameter[int64]{
			Minimum: pointer(int64(64)), Maximum: pointer(int64(3072)), Default: pointer(int64(1536)),
		},
	})
	catalog, err := newCatalog([]Config{fixed, adjustable})
	if err != nil {
		t.Fatalf("construct embedding catalog: %v", err)
	}

	fixedDefinition, _ := catalog.Lookup("fixed-embedding")
	fixedCapability, ok := fixedDefinition.Capability(api.OperationEmbedding)
	if !ok || !fixedCapability.SupportsInputModality(api.ModalityText) ||
		!fixedCapability.SupportsOutputModality(api.ModalityEmbedding) {
		t.Fatal("fixed embedding capability has the wrong operation modalities")
	}
	if dimensions, known := fixedCapability.DefaultEmbeddingDimensions(); !known || dimensions != 768 {
		t.Fatalf("fixed output dimensions were lost: %d %v", dimensions, known)
	}
	if resolved := fixedCapability.ResolveEmbeddingDimensions(pointer(int64(512))); resolved != nil {
		t.Fatalf("unsupported dimensions request was not filtered: %d", *resolved)
	}
	generation := fixedCapability.ResolveGenerationParameters(api.GenerationParameters{
		Temperature: pointer(0.5), MaxOutputTokens: pointer(int64(10)), Stop: []string{"x"},
	})
	if generation.Temperature != nil || generation.MaxOutputTokens != nil || generation.Stop != nil {
		t.Fatalf("embedding accepted generation parameters: %+v", generation)
	}

	adjustableDefinition, _ := catalog.Lookup("adjustable-embedding")
	adjustableCapability, _ := adjustableDefinition.Capability(api.OperationEmbedding)
	if dimensions, known := adjustableCapability.DefaultEmbeddingDimensions(); !known || dimensions != 1536 {
		t.Fatalf("adjustable default dimensions were lost: %d %v", dimensions, known)
	}
	if high := adjustableCapability.ResolveEmbeddingDimensions(pointer(int64(9999))); high == nil || *high != 3072 {
		t.Fatalf("embedding dimensions were not clamped to maximum: %v", high)
	}
	if low := adjustableCapability.ResolveEmbeddingDimensions(pointer(int64(1))); low == nil || *low != 64 {
		t.Fatalf("embedding dimensions were not clamped to minimum: %v", low)
	}
	if defaults := adjustableCapability.ResolveEmbeddingDimensions(nil); defaults == nil || *defaults != 1536 {
		t.Fatalf("embedding dimension default was not applied: %v", defaults)
	}
}

func TestCompleteSourceDefinesEveryModelThroughOnePath(t *testing.T) {
	configured := embeddingModelConfig("configured-embedding", &EmbeddingPolicy{})
	custom := embeddingModelConfig("custom-embedding", &EmbeddingPolicy{FixedDimensions: pointer(int64(1024))})
	custom.TokenLimits.MaxInputTokens = 4096
	custom.Capability.InputModalities = []api.Modality{api.ModalityText, api.ModalityImage}
	catalog, err := newCatalog([]Config{configured, custom})
	if err != nil {
		t.Fatalf("construct complete source catalog: %v", err)
	}
	configuredDefinition, _ := catalog.Lookup("configured-embedding")
	if _, ok := configuredDefinition.Capability(api.OperationEmbedding); !ok {
		t.Fatal("configured source model was not retained")
	}
	customDefinition, err := catalog.Lookup("custom-embedding")
	if err != nil || customDefinition.TokenLimits().MaxInputTokens != 4096 || customDefinition.Lifecycle() != LifecycleActive {
		t.Fatalf("custom source model was not admitted: %+v %v", customDefinition, err)
	}
	capability, ok := customDefinition.Capability(api.OperationEmbedding)
	if !ok || !capability.SupportsInputModality(api.ModalityImage) {
		t.Fatal("custom source model capability was not retained")
	}
}

func TestBuiltInCatalogCoversReferenceModelsWithoutAliasesOrServiceState(t *testing.T) {
	catalog, err := newSeedCatalog()
	if err != nil {
		t.Fatalf("load built-in catalog: %v", err)
	}
	if len(catalog.definitions) == 0 {
		t.Fatal("built-in catalog is empty")
	}
	for _, definition := range catalog.definitions {
		if strings.Contains(definition.BaseModel(), "/") {
			t.Fatalf("static seed BaseModel contains a namespace: %q", definition.BaseModel())
		}
		if !definition.config.Capability.Operation.IsValid() {
			t.Fatalf("static seed model %q has no valid capability", definition.BaseModel())
		}
	}

	assertOperation(t, catalog, "DeepSeek-V4.1-Flash", api.OperationGenerate)
	assertOperation(t, catalog, "gpt-image-2.5-flare", api.OperationImageGeneration)
	assertOperation(t, catalog, "glm-5-3-flash", api.OperationGenerate)
	assertOperation(t, catalog, "Kimi-K3", api.OperationGenerate)
	assertOperation(t, catalog, "claude-opus-4.8", api.OperationGenerate)
	assertOperation(t, catalog, "dall-e-3", api.OperationImageGeneration)
	assertOperation(t, catalog, "gpt-image-1", api.OperationImageGeneration)
	assertOperation(t, catalog, "gemini-2.0-flash-exp-image-generation", api.OperationImageGeneration)
	assertOperation(t, catalog, "veo-2", api.OperationVideoGeneration)
	assertOperation(t, catalog, "gpt-realtime-1.5", api.OperationRealtime)
	assertOperation(t, catalog, "gemini-embedding-2", api.OperationEmbedding)
	for _, baseModel := range []string{"claude-opus-4.8", "nova-pro-v1"} {
		assertOperation(t, catalog, baseModel, api.OperationGenerate)
	}

	gpt4Turbo, _ := catalog.Lookup("chatgpt-4o")
	limits := gpt4Turbo.TokenLimits()
	if limits.MaxInputTokens != 128000 || limits.MaxOutputTokens != 4096 {
		t.Fatalf("chatgpt-4o limits do not match the seed facts: %+v", limits)
	}
	gpt4TurboCapability, _ := gpt4Turbo.Capability(api.OperationGenerate)
	if resolved := gpt4TurboCapability.ResolveGenerationParameters(api.GenerationParameters{}); resolved.MaxOutputTokens == nil || *resolved.MaxOutputTokens != 4096 {
		t.Fatalf("central max_output_tokens policy is wrong: %+v", resolved)
	}
	latest, _ := catalog.Lookup("claude-opus-4.8")
	latestCapability, _ := latest.Capability(api.OperationGenerate)
	filtered := latestCapability.ResolveGenerationParameters(api.GenerationParameters{Temperature: pointer(0.4), TopP: pointer(0.5)})
	if filtered.Temperature != nil || filtered.TopP != nil {
		t.Fatalf("unsupported sampling parameters leaked: %+v", filtered)
	}
	for _, validEmbedding := range []string{
		"titan-embed-text", "gemini-embedding-2",
		"nova-2-multimodal-embeddings", "voyage-3-large",
	} {
		assertOperation(t, catalog, validEmbedding, api.OperationEmbedding)
	}

	decodedCatalog, err := decodeCatalogTestData(catalogTestData)
	if err != nil {
		t.Fatalf("decode embedded catalog artifact: %v", err)
	}
	for _, forbidden := range [][]byte{
		[]byte(`"provider":`), []byte(`"price":`), []byte(`"pricing":`),
		[]byte(`"protocol":`), []byte(`"endpoint":`), []byte(`"credential":`), []byte(`"family":`),
		[]byte(`"modes":`), []byte(`"usage":`),
	} {
		if bytes.Contains(decodedCatalog, forbidden) {
			t.Errorf("catalog contains forbidden owner field %s", forbidden)
		}
	}

	if len(catalogTestData) == 0 {
		t.Fatal("catalog test data is empty")
	}
	var document catalogTestDocument
	decoder := json.NewDecoder(bytes.NewReader(decodedCatalog))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&document); err != nil {
		t.Fatalf("decode embedded catalog: %v", err)
	}
	if document.SchemaVersion != catalogTestSchemaVersion || len(document.Models) == 0 {
		t.Fatalf("catalog test data has an invalid static shape: schema=%d models=%d", document.SchemaVersion, len(document.Models))
	}
	if bytes.Contains(decodedCatalog, []byte(`"sources"`)) {
		t.Fatal("embedded catalog must not retain upstream source metadata")
	}
}

func TestBuiltInEmbeddingModelsHaveIndependentCapabilities(t *testing.T) {
	catalog, err := newSeedCatalog()
	if err != nil {
		t.Fatalf("load built-in catalog: %v", err)
	}
	large, err := catalog.Lookup("gemini-embedding-2")
	if err != nil {
		t.Fatalf("lookup fixed-width embedding: %v", err)
	}
	capability, ok := large.Capability(api.OperationEmbedding)
	if !ok || !capability.SupportsInputModality(api.ModalityText) ||
		!capability.SupportsOutputModality(api.ModalityEmbedding) {
		t.Fatal("gemini-embedding-2 has an invalid embedding shape")
	}
	if resolved := capability.ResolveEmbeddingDimensions(pointer(int64(9000))); resolved != nil {
		t.Fatalf("fixed-width model accepted a dimensions parameter: %v", resolved)
	}
	if dimensions, known := capability.DefaultEmbeddingDimensions(); !known || dimensions != 3072 {
		t.Fatalf("embedding default dimension is wrong: %d %v", dimensions, known)
	}

	titan, err := catalog.Lookup("titan-embed-text")
	if err != nil {
		t.Fatalf("lookup Titan embedding: %v", err)
	}
	titanCapability, _ := titan.Capability(api.OperationEmbedding)
	if requested := titanCapability.ResolveEmbeddingDimensions(pointer(int64(512))); requested != nil {
		t.Fatalf("fixed-width embedding accepted a dimensions override: %v", requested)
	}
	if dimensions, known := titanCapability.DefaultEmbeddingDimensions(); !known || dimensions != 1024 {
		t.Fatalf("fixed embedding width is wrong: %d %v", dimensions, known)
	}

	multimodal, _ := catalog.Lookup("nova-2-multimodal-embeddings")
	multimodalCapability, _ := multimodal.Capability(api.OperationEmbedding)
	for _, modality := range []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo} {
		if !multimodalCapability.SupportsInputModality(modality) {
			t.Errorf("multimodal embedding is missing %q input", modality)
		}
	}
	if generation := multimodalCapability.ResolveGenerationParameters(api.GenerationParameters{Temperature: pointer(1.0)}); generation.Temperature != nil {
		t.Fatal("embedding inherited generation parameters")
	}
}

func TestCatalogRejectsInvalidSourceConfigs(t *testing.T) {
	tests := []struct {
		name    string
		configs []Config
		field   string
	}{
		{name: "empty BaseModel", configs: []Config{testModelConfig("")}, field: "base_model"},
		{name: "empty lifecycle", configs: []Config{withEmptyLifecycle(testModelConfig("model-a"))}, field: "lifecycle"},
		{name: "duplicate model", configs: []Config{testModelConfig("model-a"), testModelConfig("model-a")}, field: "base_model"},
		{name: "negative token limit", configs: []Config{withNegativeLimit(testModelConfig("model-a"))}, field: "token_limits"},
		{name: "reversed output-token bounds", configs: []Config{withReversedOutputLimits(testModelConfig("model-a"))}, field: "token_limits"},
		{name: "limit exceeds context", configs: []Config{withLimitAboveContext(testModelConfig("model-a"))}, field: "token_limits"},
		{name: "invalid operation", configs: []Config{withInvalidOperation(testModelConfig("model-a"))}, field: "capability.operation"},
		{name: "invalid input modality", configs: []Config{withInvalidInputModality(testModelConfig("model-a"))}, field: "capability.input_modalities"},
		{name: "invalid output modality", configs: []Config{withInvalidOutputModality(testModelConfig("model-a"))}, field: "capability.output_modalities"},
		{name: "empty modalities", configs: []Config{withEmptyInputModalities(testModelConfig("model-a"))}, field: "capability"},
		{name: "duplicate feature", configs: []Config{withDuplicateFeature(testModelConfig("model-a"))}, field: "capability.features"},
		{name: "service-owned feature", configs: []Config{withUsageFeature(testModelConfig("model-a"))}, field: "capability.features"},
		{name: "reversed numeric bounds", configs: []Config{withReversedNumericBounds(testModelConfig("model-a"))}, field: "capability.generation.temperature.bounds"},
		{name: "invalid numeric default", configs: []Config{withInvalidNumericDefault(testModelConfig("model-a"))}, field: "capability.generation.temperature.default"},
		{name: "non-finite numeric value", configs: []Config{withNonFiniteDefault(testModelConfig("model-a"))}, field: "capability.generation.temperature.default"},
		{name: "temperature clamp outside public domain", configs: []Config{withTemperatureMaximum(testModelConfig("model-a"), 3)}, field: "capability.generation.temperature.maximum"},
		{name: "top-p clamp outside public domain", configs: []Config{withTopPMinimum(testModelConfig("model-a"), 0)}, field: "capability.generation.top_p.minimum"},
		{name: "empty stop default", configs: []Config{withStopDefault(testModelConfig("model-a"), []string{""})}, field: "capability.generation.stop.default"},
		{name: "too many stop defaults", configs: []Config{withStopDefault(testModelConfig("model-a"), make([]string, 17))}, field: "capability.generation.stop.default"},
		{name: "long stop default", configs: []Config{withStopDefault(testModelConfig("model-a"), []string{strings.Repeat("界", 257)})}, field: "capability.generation.stop.default"},
		{name: "rerank generation policy", configs: []Config{withRerankOperation(testModelConfig("model-a"))}, field: "capability"},
		{name: "embedding generation policy", configs: []Config{withEmbeddingGenerationPolicy(embeddingModelConfig("model-a", &EmbeddingPolicy{}))}, field: "capability"},
		{name: "embedding missing policy", configs: []Config{withMissingEmbeddingPolicy(embeddingModelConfig("model-a", &EmbeddingPolicy{}))}, field: "capability"},
		{name: "embedding wrong output", configs: []Config{withWrongEmbeddingOutput(embeddingModelConfig("model-a", &EmbeddingPolicy{}))}, field: "capability"},
		{name: "non-embedding embedding policy", configs: []Config{withEmbeddingPolicyOnGenerate(testModelConfig("model-a"))}, field: "capability"},
		{name: "empty reasoning modes", configs: []Config{withReasoningPolicy(testModelConfig("model-a"), &ReasoningPolicy{})}, field: "capability.reasoning.thinking_modes"},
		{name: "invalid reasoning mode", configs: []Config{withReasoningPolicy(testModelConfig("model-a"), &ReasoningPolicy{ThinkingModes: []ThinkingModePolicy{{Thinking: "unknown"}}})}, field: "capability.reasoning.thinking_modes[0].thinking"},
		{name: "duplicate reasoning mode", configs: []Config{withReasoningPolicy(testModelConfig("model-a"), &ReasoningPolicy{ThinkingModes: []ThinkingModePolicy{{Thinking: api.ThinkingEnabled}, {Thinking: api.ThinkingEnabled}}})}, field: "capability.reasoning.thinking_modes"},
		{name: "disabled reasoning effort", configs: []Config{withReasoningPolicy(testModelConfig("model-a"), &ReasoningPolicy{ThinkingModes: []ThinkingModePolicy{{Thinking: api.ThinkingDisabled, Efforts: []api.ReasoningEffort{api.ReasoningEffortHigh}}}})}, field: "capability.reasoning.thinking_modes[0].efforts"},
		{name: "reasoning default not declared", configs: []Config{withReasoningPolicy(testModelConfig("model-a"), &ReasoningPolicy{ThinkingModes: []ThinkingModePolicy{{Thinking: api.ThinkingEnabled}}, DefaultThinking: pointer(api.ThinkingAdaptive)})}, field: "capability.reasoning.default_thinking"},
		{name: "reasoning effort default not declared", configs: []Config{withReasoningPolicy(testModelConfig("model-a"), &ReasoningPolicy{ThinkingModes: []ThinkingModePolicy{{Thinking: api.ThinkingEnabled, Efforts: []api.ReasoningEffort{api.ReasoningEffortLow}, DefaultEffort: pointer(api.ReasoningEffortHigh)}}})}, field: "capability.reasoning.thinking_modes[0].default_effort"},
		{name: "reasoning on embedding", configs: []Config{withReasoningPolicy(embeddingModelConfig("model-a", &EmbeddingPolicy{}), &ReasoningPolicy{ThinkingModes: []ThinkingModePolicy{{Thinking: api.ThinkingDisabled}}})}, field: "capability"},
		{name: "conflicting dimensions", configs: []Config{embeddingModelConfig("model-a", &EmbeddingPolicy{FixedDimensions: pointer(int64(128)), Dimensions: &NumericParameter[int64]{}})}, field: "capability.embedding.dimensions"},
		{name: "invalid fixed dimensions", configs: []Config{embeddingModelConfig("model-a", &EmbeddingPolicy{FixedDimensions: pointer(int64(0))})}, field: "capability.embedding.fixed_dimensions"},
		{name: "invalid adjustable dimensions", configs: []Config{embeddingModelConfig("model-a", &EmbeddingPolicy{Dimensions: &NumericParameter[int64]{Minimum: pointer(int64(0))}})}, field: "capability.embedding.dimensions.minimum"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := newCatalog(test.configs)
			var configErrorValue *ConfigError
			if !errors.As(err, &configErrorValue) || configErrorValue.Field != test.field {
				t.Fatalf("expected config error for %q, got %T %v", test.field, err, err)
			}
		})
	}
}

func TestCatalogPreservesCanonicalBaseModel(t *testing.T) {
	config := testModelConfig("model:0")
	catalog, err := newCatalog([]Config{config})
	if err != nil {
		t.Fatalf("construct catalog with a colon-bearing bare name: %v", err)
	}
	definition, err := catalog.Lookup("model:0")
	if err != nil {
		t.Fatalf("lookup bare identity: %v", err)
	}
	if definition.BaseModel() != "model:0" {
		t.Fatalf("definition changed the authoritative identity: %q", definition.BaseModel())
	}
}

func TestCatalogRejectsInvalidCapabilityShapes(t *testing.T) {
	realtimeWithoutConversationalInput := testModelConfig("realtime-without-conversational-input")
	realtimeWithoutConversationalInput.Capability.Operation = api.OperationRealtime
	realtimeWithoutConversationalInput.Capability.InputModalities = []api.Modality{api.ModalityImage}
	realtimeWithoutConversationalInput.Capability.OutputModalities = []api.Modality{api.ModalityText}

	embeddingInput := embeddingModelConfig("embedding-input", &EmbeddingPolicy{FixedDimensions: pointer(int64(768))})
	embeddingInput.Capability.InputModalities = []api.Modality{api.ModalityEmbedding}

	embeddingOutput := embeddingModelConfig("embedding-output", &EmbeddingPolicy{FixedDimensions: pointer(int64(768))})
	embeddingOutput.Capability.OutputModalities = []api.Modality{api.ModalityEmbedding, api.ModalityText}

	for _, test := range []struct {
		name   string
		config Config
	}{
		{name: "realtime requires conversational input", config: realtimeWithoutConversationalInput},
		{name: "embedding cannot be input", config: embeddingInput},
		{name: "embedding output is exclusive", config: embeddingOutput},
	} {
		t.Run(test.name, func(t *testing.T) {
			_, err := newCatalog([]Config{test.config})
			var configErr *ConfigError
			if !errors.As(err, &configErr) || configErr.Field != "capability" {
				t.Fatalf("expected capability-shape error, got %T %v", err, err)
			}
		})
	}
}

func TestCatalogRetainsStructuredOutputModelCompatibilityEvidence(t *testing.T) {
	config := testModelConfig("structured-override")
	config.Capability.Features = []api.Feature{api.FeatureStructured}
	catalog, err := newCatalog([]Config{config})
	if err != nil {
		t.Fatal(err)
	}
	definition, err := catalog.Lookup(config.BaseModel)
	if err != nil {
		t.Fatal(err)
	}
	capability, ok := definition.Capability(api.OperationGenerate)
	if !ok || !capability.SupportsFeature(api.FeatureStructured) {
		t.Fatal("structured output model compatibility evidence was not retained")
	}
}

func TestCatalogRetainsExplicitMultimodalCapabilityFacts(t *testing.T) {
	realtime := testModelConfig("realtime-multimodal")
	realtime.Capability = CapabilityConfig{
		Operation:        api.OperationRealtime,
		InputModalities:  []api.Modality{api.ModalityAudio, api.ModalityImage, api.ModalityText, api.ModalityVideo},
		OutputModalities: []api.Modality{api.ModalityAudio, api.ModalityText},
	}
	image := testModelConfig("image-multimodal")
	image.Capability = CapabilityConfig{
		Operation:        api.OperationImageGeneration,
		InputModalities:  []api.Modality{api.ModalityImage, api.ModalityText},
		OutputModalities: []api.Modality{api.ModalityImage, api.ModalityText},
	}
	catalog, err := newCatalog([]Config{realtime, image})
	if err != nil {
		t.Fatal(err)
	}
	definition, _ := catalog.Lookup(realtime.BaseModel)
	capability, _ := definition.Capability(api.OperationRealtime)
	if !capability.SupportsInputModality(api.ModalityImage) || !capability.SupportsInputModality(api.ModalityVideo) {
		t.Fatal("realtime model modalities were narrowed to the current invocation profile")
	}
	definition, _ = catalog.Lookup(image.BaseModel)
	capability, _ = definition.Capability(api.OperationImageGeneration)
	if !capability.SupportsOutputModality(api.ModalityText) || !capability.SupportsOutputModality(api.ModalityImage) {
		t.Fatal("explicit multimodal image output was rejected")
	}
}

func TestCatalogDocumentErrorsAreTyped(t *testing.T) {
	tests := []string{
		`{"schema_version":2,"models":[]}`,
		`{"schema_version":3,"models":[]}`,
		`{"schema_version":3,"models":[]} {}`,
		`{"schema_version":3,"models":[],"sources":[]}`,
	}
	for _, data := range tests {
		_, err := loadCatalogTestData([]byte(data))
		var catalogError *CatalogDataError
		if !errors.As(err, &catalogError) {
			t.Fatalf("catalog document error is not typed: %T %v", err, err)
		}
	}
}

func TestCatalogDocumentAcceptsOnlyCurrentSchema(t *testing.T) {
	document := catalogTestDocument{
		SchemaVersion: catalogTestSchemaVersion,
		Models:        []Config{testModelConfig("model-a")},
	}
	encoded, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := loadCatalogTestData(encoded); err != nil {
		t.Fatalf("current schema was rejected: %v", err)
	}
	document.SchemaVersion--
	encoded, err = json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := loadCatalogTestData(encoded); err == nil {
		t.Fatal("previous schema was accepted")
	}
}

func TestModelErrorsPreserveBoundaryFacts(t *testing.T) {
	configErrorValue := (&ConfigError{BaseModel: "m", Field: "capability", Reason: "missing"}).Error()
	if configErrorValue != `invalid model config "m" capability: missing` {
		t.Fatalf("unexpected config error: %q", configErrorValue)
	}
	notFoundErrorValue := (&ModelNotFoundError{BaseModel: "missing"}).Error()
	if notFoundErrorValue != `model "missing" is not present in the catalog` {
		t.Fatalf("unexpected lookup error: %q", notFoundErrorValue)
	}
	catalogErrorValue := (&CatalogDataError{Reason: "broken"}).Error()
	if catalogErrorValue != "invalid model catalog: broken" {
		t.Fatalf("unexpected catalog error: %q", catalogErrorValue)
	}
}

func TestBuiltInClaudeModelsFilterUnsupportedSamplingParameters(t *testing.T) {
	catalog, err := newSeedCatalog()
	if err != nil {
		t.Fatalf("load built-in catalog: %v", err)
	}
	for _, baseModel := range []string{"claude-opus-4.8", "claude-opus-latest", "claude-sonnet-4.6"} {
		definition, lookupErr := catalog.Lookup(baseModel)
		if lookupErr != nil {
			t.Fatalf("lookup %q: %v", baseModel, lookupErr)
		}
		capability, ok := definition.Capability(api.OperationGenerate)
		if !ok {
			t.Fatalf("%q has no generate capability", baseModel)
		}
		resolved := capability.ResolveGenerationParameters(api.GenerationParameters{
			Temperature: pointer(0.4),
			TopP:        pointer(0.5),
		})
		if resolved.Temperature != nil || resolved.TopP != nil {
			t.Fatalf("%q accepted unsupported sampling parameters: %+v", baseModel, resolved)
		}
	}
}

func assertOperation(t *testing.T, catalog Catalog, baseModel string, operation api.Operation) {
	t.Helper()
	definition, err := catalog.Lookup(baseModel)
	if err != nil {
		t.Fatalf("lookup %q: %v", baseModel, err)
	}
	if _, ok := definition.Capability(operation); !ok {
		t.Fatalf("model %q is missing operation %q", baseModel, operation)
	}
}

func testModelConfig(baseModel string) Config {
	return Config{
		BaseModel: baseModel,
		Lifecycle: LifecycleActive,
		TokenLimits: TokenLimits{
			ContextWindowTokens: 8192,
			MaxInputTokens:      7000,
			MinOutputTokens:     1,
			MaxOutputTokens:     2048,
		},
		Capability: CapabilityConfig{
			Operation:        api.OperationGenerate,
			InputModalities:  []api.Modality{api.ModalityText},
			OutputModalities: []api.Modality{api.ModalityText},
			Generation: &GenerationPolicy{
				MaxOutputTokens: &OutputTokenParameter{},
				Stop:            &StopParameter{Default: []string{"END"}},
			},
		},
	}
}

func reasoningModelConfig(baseModel string) Config {
	config := testModelConfig(baseModel)
	defaultThinking := api.ThinkingAdaptive
	enabledDefault := api.ReasoningEffortMedium
	adaptiveDefault := api.ReasoningEffortMedium
	config.Capability.Reasoning = &ReasoningPolicy{
		ThinkingModes: []ThinkingModePolicy{
			{Thinking: api.ThinkingDisabled},
			{
				Thinking:      api.ThinkingEnabled,
				Efforts:       []api.ReasoningEffort{api.ReasoningEffortLow, api.ReasoningEffortMedium, api.ReasoningEffortHigh},
				DefaultEffort: &enabledDefault,
			},
			{
				Thinking:      api.ThinkingAdaptive,
				Efforts:       []api.ReasoningEffort{api.ReasoningEffortLow, api.ReasoningEffortMedium, api.ReasoningEffortHigh},
				DefaultEffort: &adaptiveDefault,
			},
		},
		DefaultThinking: &defaultThinking,
	}
	return config
}

func withReasoningPolicy(config Config, policy *ReasoningPolicy) Config {
	config.Capability.Reasoning = policy
	return config
}

func embeddingModelConfig(baseModel string, policy *EmbeddingPolicy) Config {
	return Config{
		BaseModel: baseModel,
		Lifecycle: LifecycleActive,
		TokenLimits: TokenLimits{
			MaxInputTokens: 8192,
		},
		Capability: CapabilityConfig{
			Operation:        api.OperationEmbedding,
			InputModalities:  []api.Modality{api.ModalityText},
			OutputModalities: []api.Modality{api.ModalityEmbedding},
			Embedding:        policy,
		},
	}
}

func withEmptyLifecycle(config Config) Config {
	config.Lifecycle = ""
	return config
}

func withNegativeLimit(config Config) Config {
	config.TokenLimits.MaxInputTokens = -1
	return config
}

func withReversedOutputLimits(config Config) Config {
	config.TokenLimits.MinOutputTokens = config.TokenLimits.MaxOutputTokens + 1
	return config
}

func withLimitAboveContext(config Config) Config {
	config.TokenLimits.MaxInputTokens = config.TokenLimits.ContextWindowTokens + 1
	return config
}

func withInvalidOperation(config Config) Config {
	config.Capability.Operation = "invalid"
	return config
}

func withInvalidInputModality(config Config) Config {
	config.Capability.InputModalities = []api.Modality{"invalid"}
	return config
}

func withInvalidOutputModality(config Config) Config {
	config.Capability.OutputModalities = []api.Modality{"invalid"}
	return config
}

func withEmptyInputModalities(config Config) Config {
	config.Capability.InputModalities = nil
	return config
}

func withDuplicateFeature(config Config) Config {
	config.Capability.Features = []api.Feature{api.FeatureToolCalls, api.FeatureToolCalls}
	return config
}

func withUsageFeature(config Config) Config {
	config.Capability.Features = []api.Feature{api.FeatureUsage}
	return config
}

func withReversedNumericBounds(config Config) Config {
	config.Capability.Generation.Temperature = &NumericParameter[float64]{
		Minimum: pointer(2.0), Maximum: pointer(1.0),
	}
	return config
}

func withInvalidNumericDefault(config Config) Config {
	config.Capability.Generation.Temperature = &NumericParameter[float64]{
		Minimum: pointer(0.0), Maximum: pointer(1.0), Default: pointer(2.0),
	}
	return config
}

func withNonFiniteDefault(config Config) Config {
	config.Capability.Generation.Temperature = &NumericParameter[float64]{Default: pointer(math.Inf(1))}
	return config
}

func withTemperatureMaximum(config Config, maximum float64) Config {
	config.Capability.Generation.Temperature = &NumericParameter[float64]{Maximum: &maximum}
	return config
}

func withTopPMinimum(config Config, minimum float64) Config {
	config.Capability.Generation.TopP = &NumericParameter[float64]{Minimum: &minimum}
	return config
}

func withStopDefault(config Config, sequences []string) Config {
	config.Capability.Generation.Stop = &StopParameter{Default: sequences}
	return config
}

func withRerankOperation(config Config) Config {
	config.Capability.Operation = api.OperationRerank
	return config
}

func withEmbeddingGenerationPolicy(config Config) Config {
	config.Capability.Generation = &GenerationPolicy{}
	return config
}

func withMissingEmbeddingPolicy(config Config) Config {
	config.Capability.Embedding = nil
	return config
}

func withWrongEmbeddingOutput(config Config) Config {
	config.Capability.OutputModalities = []api.Modality{api.ModalityText}
	return config
}

func withEmbeddingPolicyOnGenerate(config Config) Config {
	config.Capability.Embedding = &EmbeddingPolicy{}
	return config
}

func pointer[T any](value T) *T {
	return &value
}
