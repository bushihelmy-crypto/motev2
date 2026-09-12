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

func TestCatalogFreezesDefaultsAndKernelOverrides(t *testing.T) {
	defaults := []Config{testModelConfig("default-model"), testModelConfig("overridden-model")}
	overrideLifecycle := LifecycleDeprecated
	overrideContext := int64(4096)
	overrideInput := int64(3500)
	overrideMinimum := int64(2)
	overrideMaximum := int64(1024)
	overrides := []Override{{
		BaseModel: "overridden-model",
		Lifecycle: &overrideLifecycle,
		TokenLimits: TokenLimitsOverride{
			ContextWindowTokens: &overrideContext,
			MaxInputTokens:      &overrideInput,
			MinOutputTokens:     &overrideMinimum,
			MaxOutputTokens:     &overrideMaximum,
		},
		Capability: &CapabilityConfig{
			Operation:        api.OperationGenerate,
			InputModalities:  []api.Modality{api.ModalityText, api.ModalityImage},
			OutputModalities: []api.Modality{api.ModalityText},
			Generation: &GenerationPolicy{
				MaxOutputTokens: &OutputTokenParameter{},
			},
		},
	}}

	catalog, err := newCatalog(defaults, overrides)
	if err != nil {
		t.Fatalf("construct catalog: %v", err)
	}

	// Constructor inputs cease to be state once effective definitions exist.
	defaults[0].Capability.InputModalities[0] = api.ModalityAudio
	overrideLifecycle = LifecycleRetired
	overrideContext = 1
	overrideInput = 1
	overrideMinimum = 1
	overrideMaximum = 1
	overrides[0].Capability.InputModalities[0] = api.ModalityAudio
	overrides[0].Capability.Generation.MaxOutputTokens = nil

	inherited, err := catalog.Lookup("default-model")
	if err != nil {
		t.Fatalf("lookup inherited model: %v", err)
	}
	inheritedCapability, ok := inherited.Capability(api.OperationGenerate)
	if !ok || !inheritedCapability.SupportsInputModality(api.ModalityText) || inheritedCapability.SupportsInputModality(api.ModalityAudio) {
		t.Fatal("default model did not retain its frozen capability")
	}

	definition, err := catalog.Lookup("overridden-model")
	if err != nil {
		t.Fatalf("lookup overridden model: %v", err)
	}
	if definition.BaseModel() != "overridden-model" || definition.Lifecycle() != LifecycleDeprecated {
		t.Fatalf("Kernel scalar override was not applied: base_model=%q lifecycle=%q", definition.BaseModel(), definition.Lifecycle())
	}
	limits := definition.TokenLimits()
	if limits.ContextWindowTokens != 4096 || limits.MaxInputTokens != 3500 || limits.MinOutputTokens != 2 || limits.MaxOutputTokens != 1024 {
		t.Fatalf("Kernel token override was not applied: %+v", limits)
	}
	capability, ok := definition.Capability(api.OperationGenerate)
	if !ok || !capability.SupportsInputModality(api.ModalityText) || !capability.SupportsInputModality(api.ModalityImage) {
		t.Fatal("Kernel operation replacement was not applied")
	}
	if capability.SupportsFeature(api.FeatureUsage) {
		t.Fatal("a non-nil Kernel capability must replace, not patch, the catalog capability")
	}
	parameters := capability.ResolveGenerationParameters(api.GenerationParameters{})
	if parameters.MaxOutputTokens == nil || *parameters.MaxOutputTokens != 1024 {
		t.Fatalf("central output-token policy was not clamped to the override limit: %+v", parameters)
	}

	_, err = catalog.Lookup("overridden-model ")
	var notFound *ModelNotFoundError
	if !errors.As(err, &notFound) || notFound.ModelID != "overridden-model " {
		t.Fatalf("lookup must use the exact model ID without aliases: %T %v", err, err)
	}
}

func TestCapabilityResolvesDefaultsRequestsFilteringAndKnownBounds(t *testing.T) {
	catalog, err := newCatalog([]Config{testModelConfig("model-a")}, nil)
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
		TopP:            &NumericParameter[float64]{Minimum: pointer(0.0), Maximum: pointer(1.0)},
		MaxOutputTokens: &OutputTokenParameter{},
		Stop:            &StopParameter{},
		Seed:            &NumericParameter[int64]{Minimum: pointer(int64(0)), Maximum: pointer(int64(100))},
	}
	allCatalog, err := newCatalog([]Config{allSupported}, nil)
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

func TestCapabilityClampsOnlyKnownNumericBoundaries(t *testing.T) {
	config := testModelConfig("bounded-model")
	config.Capability.Generation = &GenerationPolicy{
		Temperature:     &NumericParameter[float64]{Minimum: pointer(0.0), Maximum: pointer(2.0)},
		TopP:            &NumericParameter[float64]{Maximum: pointer(1.0)},
		MaxOutputTokens: &OutputTokenParameter{},
		Seed:            &NumericParameter[int64]{Minimum: pointer(int64(0))},
	}
	catalog, err := newCatalog([]Config{config}, nil)
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
	unknownCatalog, err := newCatalog([]Config{unknownBounds}, nil)
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
	catalog, err := newCatalog([]Config{fixed, adjustable}, nil)
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

func TestKernelCanOverrideBuiltinsAndDefineCustomModelsThroughOnePath(t *testing.T) {
	customInput := int64(4096)
	catalog, err := newCatalog([]Config{testModelConfig("built-in")}, []Override{
		{
			BaseModel: "built-in",
			Capability: &CapabilityConfig{
				Operation:        api.OperationEmbedding,
				InputModalities:  []api.Modality{api.ModalityText},
				OutputModalities: []api.Modality{api.ModalityEmbedding},
				Embedding:        &EmbeddingPolicy{},
			},
		},
		{
			BaseModel:   "custom-embedding",
			TokenLimits: TokenLimitsOverride{MaxInputTokens: &customInput},
			Capability: &CapabilityConfig{
				Operation:        api.OperationEmbedding,
				InputModalities:  []api.Modality{api.ModalityText, api.ModalityImage},
				OutputModalities: []api.Modality{api.ModalityEmbedding},
				Embedding:        &EmbeddingPolicy{FixedDimensions: pointer(int64(1024))},
			},
		},
	})
	if err != nil {
		t.Fatalf("construct catalog with Kernel models: %v", err)
	}
	builtIn, _ := catalog.Lookup("built-in")
	if _, ok := builtIn.Capability(api.OperationGenerate); ok {
		t.Fatal("built-in operation replacement left a second capability truth")
	}
	custom, err := catalog.Lookup("custom-embedding")
	if err != nil || custom.TokenLimits().MaxInputTokens != 4096 || custom.Lifecycle() != LifecycleActive {
		t.Fatalf("custom Kernel model was not admitted: %+v %v", custom, err)
	}
	capability, ok := custom.Capability(api.OperationEmbedding)
	if !ok || !capability.SupportsInputModality(api.ModalityImage) {
		t.Fatal("custom Kernel model capability was not retained")
	}
}

func TestBuiltInCatalogCoversReferenceModelsWithoutAliasesOrServiceState(t *testing.T) {
	catalog, err := NewCatalog(nil)
	if err != nil {
		t.Fatalf("load built-in catalog: %v", err)
	}
	if len(catalog.definitions) == 0 {
		t.Fatal("built-in catalog is empty")
	}
	for _, definition := range catalog.definitions {
		if strings.Contains(definition.BaseModel(), "/") {
			t.Fatalf("generated BaseModel still contains a source prefix: %q", definition.BaseModel())
		}
		if !definition.config.Capability.Operation.IsValid() {
			t.Fatalf("generated model %q has no valid capability", definition.BaseModel())
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
	for _, generated := range []string{"claude-opus-4.8", "nova-pro-v1"} {
		assertOperation(t, catalog, generated, api.OperationGenerate)
	}

	gpt4Turbo, _ := catalog.Lookup("chatgpt-4o")
	limits := gpt4Turbo.TokenLimits()
	if limits.MaxInputTokens != 128000 || limits.MaxOutputTokens != 4096 {
		t.Fatalf("chatgpt-4o limits do not match the source facts: %+v", limits)
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
	for _, alias := range []string{"doubao-embedding-large-text", "openrouter/hexgrad/kokoro-82m", "openrouter/google/chirp-3"} {
		if _, lookupErr := catalog.Lookup(alias); lookupErr == nil {
			t.Errorf("non-authoritative BaseModel alias was published: %q", alias)
		}
	}
	for _, conflicting := range []string{
		"gpt-6-astra", "deepseek-v4-flash", "kimi-k3", "claude-opus-5",
		"gemini-2.5-pro", "gemini-2.5-flash-image", "gpt-4.1", "sora-2",
		"text-embedding-3-large", "embed-v4.0", "codestral-embed", "mistral-embed",
		"kokoro-82m", "whisper-1", "text-to-image", "gemini-3-flash",
	} {
		if _, lookupErr := catalog.Lookup(conflicting); lookupErr == nil {
			t.Errorf("conflicting BaseModel %q was published", conflicting)
		}
	}

	for _, synthetic := range []string{
		"codex-auto-review", "claude-opus-4-8-high", "deepseek-v4-flash-max",
		"o3-mini-high", "grok-3-search", "grok-3-mini-low", "preset/advanced",
		"conservative", "edit", "openrouter/openai/text-embedding-3-large:batch",
	} {
		if _, lookupErr := catalog.Lookup(synthetic); lookupErr == nil {
			t.Errorf("synthetic request preset %q was stored as a model", synthetic)
		}
	}
	for _, rejected := range []string{
		"vercel_ai_gateway/amazon/titan-embed-text-v2",
		"vercel_ai_gateway/cohere/embed-v4.0",
		"vercel_ai_gateway/mistral/codestral-embed",
		"vercel_ai_gateway/mistral/mistral-embed",
	} {
		if _, lookupErr := catalog.Lookup(rejected); lookupErr == nil {
			t.Errorf("invalid upstream model %q was published", rejected)
		}
	}
	for _, validEmbedding := range []string{
		"titan-embed-text", "gemini-embedding-2",
		"nova-2-multimodal-embeddings", "voyage-3-large",
	} {
		assertOperation(t, catalog, validEmbedding, api.OperationEmbedding)
	}

	decodedCatalog, err := decodeCatalogData(catalogData)
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

	if len(catalogData) == 0 {
		t.Fatal("embedded catalog is empty")
	}
	var document CatalogDocument
	decoder := json.NewDecoder(bytes.NewReader(decodedCatalog))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&document); err != nil {
		t.Fatalf("decode catalog provenance: %v", err)
	}
	wantSources := map[string]string{
		// These are public source revisions/content digest. Keep the chunks
		// separate so the secret scanner does not mistake provenance for a key.
		"new-api": strings.Join([]string{
			"bdef1175", "05247769", "268b2096", "65fb3ad7", "554c3da7",
		}, ""),
		"bifrost": strings.Join([]string{
			"c5c02ae7", "47fe294a", "7f7f7652", "77aae08b", "bf0835fa",
		}, ""),
		"bifrost-model-parameters": strings.Join([]string{
			"85debda1", "2147b0ce", "b170195e", "bce4f5e5", "415f4bdd", "facd2f1e", "e769a19e", "d5b9983d",
		}, ""),
	}
	for _, source := range document.Sources {
		value := source.Revision
		if value == "" {
			value = source.SHA256
		}
		if wantSources[source.Name] != value {
			t.Errorf("unexpected source provenance for %q: %q", source.Name, value)
		}
		delete(wantSources, source.Name)
	}
	if len(wantSources) != 0 {
		t.Fatalf("catalog provenance is incomplete: %v", wantSources)
	}
}

func TestBuiltInEmbeddingModelsHaveIndependentCapabilities(t *testing.T) {
	catalog, err := NewCatalog(nil)
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
		t.Fatal("embedding inherited generation parameters from source metadata")
	}
}

func TestCatalogRejectsInvalidDefaultsAndOverrides(t *testing.T) {
	tests := []struct {
		name      string
		defaults  []Config
		overrides []Override
		field     string
	}{
		{name: "empty model ID", defaults: []Config{testModelConfig("")}, field: "base_model"},
		{name: "empty lifecycle", defaults: []Config{withEmptyLifecycle(testModelConfig("model-a"))}, field: "lifecycle"},
		{name: "duplicate model", defaults: []Config{testModelConfig("model-a"), testModelConfig("model-a")}, field: "base_model"},
		{name: "negative token limit", defaults: []Config{withNegativeLimit(testModelConfig("model-a"))}, field: "token_limits"},
		{name: "reversed output-token bounds", defaults: []Config{withReversedOutputLimits(testModelConfig("model-a"))}, field: "token_limits"},
		{name: "limit exceeds context", defaults: []Config{withLimitAboveContext(testModelConfig("model-a"))}, field: "token_limits"},
		{name: "invalid operation", defaults: []Config{withInvalidOperation(testModelConfig("model-a"))}, field: "capability.operation"},
		{name: "invalid input modality", defaults: []Config{withInvalidInputModality(testModelConfig("model-a"))}, field: "capability.input_modalities"},
		{name: "invalid output modality", defaults: []Config{withInvalidOutputModality(testModelConfig("model-a"))}, field: "capability.output_modalities"},
		{name: "empty modalities", defaults: []Config{withEmptyInputModalities(testModelConfig("model-a"))}, field: "capability"},
		{name: "duplicate feature", defaults: []Config{withDuplicateFeature(testModelConfig("model-a"))}, field: "capability.features"},
		{name: "service-owned feature", defaults: []Config{withUsageFeature(testModelConfig("model-a"))}, field: "capability.features"},
		{name: "reversed numeric bounds", defaults: []Config{withReversedNumericBounds(testModelConfig("model-a"))}, field: "capability.generation.temperature.bounds"},
		{name: "invalid numeric default", defaults: []Config{withInvalidNumericDefault(testModelConfig("model-a"))}, field: "capability.generation.temperature.default"},
		{name: "non-finite numeric value", defaults: []Config{withNonFiniteDefault(testModelConfig("model-a"))}, field: "capability.generation.temperature.value"},
		{name: "rerank generation policy", defaults: []Config{withRerankOperation(testModelConfig("model-a"))}, field: "capability"},
		{name: "embedding generation policy", defaults: []Config{withEmbeddingGenerationPolicy(embeddingModelConfig("model-a", &EmbeddingPolicy{}))}, field: "capability"},
		{name: "embedding missing policy", defaults: []Config{withMissingEmbeddingPolicy(embeddingModelConfig("model-a", &EmbeddingPolicy{}))}, field: "capability"},
		{name: "embedding wrong output", defaults: []Config{withWrongEmbeddingOutput(embeddingModelConfig("model-a", &EmbeddingPolicy{}))}, field: "capability"},
		{name: "non-embedding embedding policy", defaults: []Config{withEmbeddingPolicyOnGenerate(testModelConfig("model-a"))}, field: "capability"},
		{name: "conflicting dimensions", defaults: []Config{embeddingModelConfig("model-a", &EmbeddingPolicy{FixedDimensions: pointer(int64(128)), Dimensions: &NumericParameter[int64]{}})}, field: "capability.embedding.dimensions"},
		{name: "invalid fixed dimensions", defaults: []Config{embeddingModelConfig("model-a", &EmbeddingPolicy{FixedDimensions: pointer(int64(0))})}, field: "capability.embedding.fixed_dimensions"},
		{name: "invalid adjustable dimensions", defaults: []Config{embeddingModelConfig("model-a", &EmbeddingPolicy{Dimensions: &NumericParameter[int64]{Minimum: pointer(int64(0))}})}, field: "capability.embedding.dimensions.minimum"},
		{name: "empty override ID", defaults: []Config{testModelConfig("model-a")}, overrides: []Override{{BaseModel: ""}}, field: "override.base_model"},
		{name: "incomplete custom model", defaults: []Config{testModelConfig("model-a")}, overrides: []Override{{BaseModel: "missing"}}, field: "capability.operation"},
		{name: "duplicate override", defaults: []Config{testModelConfig("model-a")}, overrides: []Override{{BaseModel: "model-a"}, {BaseModel: "model-a"}}, field: "override.base_model"},
		{name: "empty capability replacement", defaults: []Config{testModelConfig("model-a")}, overrides: []Override{{BaseModel: "model-a", Capability: &CapabilityConfig{}}}, field: "capability.operation"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := newCatalog(test.defaults, test.overrides)
			var configErrorValue *ConfigError
			if !errors.As(err, &configErrorValue) || configErrorValue.Field != test.field {
				t.Fatalf("expected config error for %q, got %T %v", test.field, err, err)
			}
		})
	}
}

func TestValidateBaseModelVocabulary(t *testing.T) {
	for _, test := range []struct {
		name  string
		value string
		valid bool
	}{
		{name: "empty", value: "", valid: false},
		{name: "leading whitespace", value: " model", valid: false},
		{name: "trailing whitespace", value: "model ", valid: false},
		{name: "qualified name", value: "provider/model", valid: false},
		{name: "bare name with colon", value: "model:0", valid: true},
		{name: "bare name", value: "model-v1.2", valid: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			err := ValidateBaseModel(test.value)
			if test.valid && err != nil {
				t.Fatalf("ValidateBaseModel(%q) rejected a valid bare name: %v", test.value, err)
			}
			if !test.valid && err == nil {
				t.Fatalf("ValidateBaseModel(%q) accepted an invalid identity", test.value)
			}
		})
	}

	config := testModelConfig("model:0")
	catalog, err := newCatalog([]Config{config}, nil)
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
			_, err := newCatalog([]Config{test.config}, nil)
			var configErr *ConfigError
			if !errors.As(err, &configErr) || configErr.Field != "capability" {
				t.Fatalf("expected capability-shape error, got %T %v", err, err)
			}
		})
	}
}

func TestDefaultCapabilityModalitiesAreOwnedByTheShapePolicy(t *testing.T) {
	for _, operation := range []api.Operation{
		api.OperationGenerate,
		api.OperationEmbedding,
		api.OperationRerank,
		api.OperationImageGeneration,
		api.OperationAudioGeneration,
		api.OperationAudioTranscription,
		api.OperationMusicGeneration,
		api.OperationVideoGeneration,
		api.OperationRealtime,
	} {
		inputs, outputs, known := DefaultCapabilityModalities(operation)
		if !known || len(inputs) == 0 || len(outputs) == 0 {
			t.Fatalf("missing default shape for %q: inputs=%v outputs=%v known=%v", operation, inputs, outputs, known)
		}
		inputs[0] = "mutated"
		outputs[0] = "mutated"
		againInputs, againOutputs, againKnown := DefaultCapabilityModalities(operation)
		if !againKnown || againInputs[0] == "mutated" || againOutputs[0] == "mutated" {
			t.Fatalf("shape policy returned mutable state for %q", operation)
		}
	}
	if inputs, outputs, known := DefaultCapabilityModalities("unknown"); known || inputs != nil || outputs != nil {
		t.Fatalf("unknown operation unexpectedly had defaults: inputs=%v outputs=%v known=%v", inputs, outputs, known)
	}
}

func TestCatalogRetainsStructuredOutputModelCompatibilityEvidence(t *testing.T) {
	config := testModelConfig("structured-override")
	config.Capability.Features = []api.Feature{api.FeatureStructured}
	catalog, err := newCatalog([]Config{config}, nil)
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
	catalog, err := newCatalog([]Config{realtime, image}, nil)
	if err != nil {
		t.Fatal(err)
	}
	definition, _ := catalog.Lookup(realtime.BaseModel)
	capability, _ := definition.Capability(api.OperationRealtime)
	if !capability.SupportsInputModality(api.ModalityImage) || !capability.SupportsInputModality(api.ModalityVideo) {
		t.Fatal("realtime source modalities were narrowed to the current invocation profile")
	}
	definition, _ = catalog.Lookup(image.BaseModel)
	capability, _ = definition.Capability(api.OperationImageGeneration)
	if !capability.SupportsOutputModality(api.ModalityText) || !capability.SupportsOutputModality(api.ModalityImage) {
		t.Fatal("explicit multimodal image output was rejected")
	}
}

func TestCatalogDocumentErrorsAreTyped(t *testing.T) {
	tests := []string{
		`{"schema_version":2,"sources":[{"name":"x","revision":"r"}],"models":[]}`,
		`{"schema_version":1,"sources":[],"models":[]}`,
		`{"schema_version":1,"sources":[{"name":"","revision":"r"}],"models":[]}`,
		`{"schema_version":1,"sources":[{"name":"x","revision":"r"},{"name":"x","sha256":"s"}],"models":[]}`,
		`{"schema_version":1,"sources":[{"name":"x"}],"models":[]}`,
		`{"schema_version":1,"sources":[{"name":"x","revision":"r","sha256":"s"}],"models":[]}`,
		`{"schema_version":1,"sources":[{"name":"x","revision":"r","provider":"bad"}],"models":[]}`,
		`{"schema_version":1,"sources":[{"name":"x","revision":"r"}],"models":[]} {}`,
	}
	for _, data := range tests {
		_, err := loadCatalogDefaults([]byte(data))
		var catalogError *CatalogDataError
		if !errors.As(err, &catalogError) {
			t.Fatalf("catalog document error is not typed: %T %v", err, err)
		}
	}
}

func TestCatalogDocumentAcceptsOnlyCurrentSchema(t *testing.T) {
	document := CatalogDocument{
		SchemaVersion: CatalogSchemaVersion,
		Sources:       []CatalogSource{{Name: "test", Revision: "immutable"}},
		Models:        []Config{testModelConfig("model-a")},
	}
	encoded, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := loadCatalogDefaults(encoded); err != nil {
		t.Fatalf("current schema was rejected: %v", err)
	}
	document.SchemaVersion--
	encoded, err = json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := loadCatalogDefaults(encoded); err == nil {
		t.Fatal("previous schema was accepted")
	}
}

func TestModelErrorsPreserveBoundaryFacts(t *testing.T) {
	configErrorValue := (&ConfigError{ModelID: "m", Field: "capability", Reason: "missing"}).Error()
	if configErrorValue != `invalid model config "m" capability: missing` {
		t.Fatalf("unexpected config error: %q", configErrorValue)
	}
	notFoundErrorValue := (&ModelNotFoundError{ModelID: "missing"}).Error()
	if notFoundErrorValue != `model "missing" is not present in the catalog` {
		t.Fatalf("unexpected lookup error: %q", notFoundErrorValue)
	}
	catalogErrorValue := (&CatalogDataError{Reason: "broken"}).Error()
	if catalogErrorValue != "invalid embedded model catalog: broken" {
		t.Fatalf("unexpected catalog error: %q", catalogErrorValue)
	}
}

func TestBuiltInClaudeModelsFilterUnsupportedSamplingParameters(t *testing.T) {
	catalog, err := NewCatalog(nil)
	if err != nil {
		t.Fatalf("load built-in catalog: %v", err)
	}
	for _, modelID := range []string{"claude-opus-4.8", "claude-opus-latest", "claude-sonnet-4.6"} {
		definition, lookupErr := catalog.Lookup(modelID)
		if lookupErr != nil {
			t.Fatalf("lookup %q: %v", modelID, lookupErr)
		}
		capability, ok := definition.Capability(api.OperationGenerate)
		if !ok {
			t.Fatalf("%q has no generate capability", modelID)
		}
		resolved := capability.ResolveGenerationParameters(api.GenerationParameters{
			Temperature: pointer(0.4),
			TopP:        pointer(0.5),
		})
		if resolved.Temperature != nil || resolved.TopP != nil {
			t.Fatalf("%q accepted unsupported sampling parameters: %+v", modelID, resolved)
		}
	}
}

func assertOperation(t *testing.T, catalog Catalog, modelID string, operation api.Operation) {
	t.Helper()
	definition, err := catalog.Lookup(modelID)
	if err != nil {
		t.Fatalf("lookup %q: %v", modelID, err)
	}
	if _, ok := definition.Capability(operation); !ok {
		t.Fatalf("model %q is missing operation %q", modelID, operation)
	}
}

func testModelConfig(id string) Config {
	return Config{
		BaseModel: id,
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

func embeddingModelConfig(id string, policy *EmbeddingPolicy) Config {
	return Config{
		BaseModel: id,
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
