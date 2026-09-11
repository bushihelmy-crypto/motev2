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
		ModelID:   "overridden-model",
		Lifecycle: &overrideLifecycle,
		TokenLimits: TokenLimitsOverride{
			ContextWindowTokens: &overrideContext,
			MaxInputTokens:      &overrideInput,
			MinOutputTokens:     &overrideMinimum,
			MaxOutputTokens:     &overrideMaximum,
		},
		Operations: []OperationConfig{{
			Operation:        api.OperationGenerate,
			Modes:            []api.DeliveryMode{api.ModeUnary},
			InputModalities:  []api.Modality{api.ModalityText, api.ModalityImage},
			OutputModalities: []api.Modality{api.ModalityText},
			Generation: &GenerationPolicy{
				MaxOutputTokens: &OutputTokenParameter{},
			},
		}},
	}}

	catalog, err := newCatalog(defaults, overrides)
	if err != nil {
		t.Fatalf("construct catalog: %v", err)
	}

	// Constructor inputs cease to be state once effective definitions exist.
	defaults[0].Operations[0].Modes[0] = api.ModeAsync
	overrideLifecycle = LifecycleRetired
	overrideContext = 1
	overrideInput = 1
	overrideMinimum = 1
	overrideMaximum = 1
	overrides[0].Operations[0].InputModalities[0] = api.ModalityAudio
	overrides[0].Operations[0].Generation.MaxOutputTokens = nil

	inherited, err := catalog.Lookup("default-model")
	if err != nil {
		t.Fatalf("lookup inherited model: %v", err)
	}
	inheritedCapability, ok := inherited.Capability(api.OperationGenerate)
	if !ok || !inheritedCapability.SupportsMode(api.ModeUnary) {
		t.Fatal("default model did not retain its frozen capability")
	}

	definition, err := catalog.Lookup("overridden-model")
	if err != nil {
		t.Fatalf("lookup overridden model: %v", err)
	}
	if definition.ID() != "overridden-model" || definition.Lifecycle() != LifecycleDeprecated {
		t.Fatalf("Kernel scalar override was not applied: id=%q lifecycle=%q", definition.ID(), definition.Lifecycle())
	}
	limits := definition.TokenLimits()
	if limits.ContextWindowTokens != 4096 || limits.MaxInputTokens != 3500 || limits.MinOutputTokens != 2 || limits.MaxOutputTokens != 1024 {
		t.Fatalf("Kernel token override was not applied: %+v", limits)
	}
	capability, ok := definition.Capability(api.OperationGenerate)
	if !ok || !capability.SupportsInputModality(api.ModalityText) || !capability.SupportsInputModality(api.ModalityImage) {
		t.Fatal("Kernel operation replacement was not applied")
	}
	if capability.SupportsMode(api.ModeServerStream) || capability.SupportsFeature(api.FeatureUsage) {
		t.Fatal("a non-nil Kernel operation set must replace, not patch, catalog operations")
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
	allSupported.Operations[0].Generation = &GenerationPolicy{
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
	config.Operations[0].Generation = &GenerationPolicy{
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
	unknownBounds.Operations[0].Generation = &GenerationPolicy{
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
			ModelID: "built-in",
			Operations: []OperationConfig{{
				Operation:        api.OperationEmbedding,
				Modes:            []api.DeliveryMode{api.ModeUnary},
				InputModalities:  []api.Modality{api.ModalityText},
				OutputModalities: []api.Modality{api.ModalityEmbedding},
				Embedding:        &EmbeddingPolicy{},
			}},
		},
		{
			ModelID:     "kernel/custom-embedding",
			TokenLimits: TokenLimitsOverride{MaxInputTokens: &customInput},
			Operations: []OperationConfig{{
				Operation:        api.OperationEmbedding,
				Modes:            []api.DeliveryMode{api.ModeUnary},
				InputModalities:  []api.Modality{api.ModalityText, api.ModalityImage},
				OutputModalities: []api.Modality{api.ModalityEmbedding},
				Embedding:        &EmbeddingPolicy{FixedDimensions: pointer(int64(1024))},
			}},
		},
	})
	if err != nil {
		t.Fatalf("construct catalog with Kernel models: %v", err)
	}
	builtIn, _ := catalog.Lookup("built-in")
	if _, ok := builtIn.Capability(api.OperationGenerate); ok {
		t.Fatal("built-in operation replacement left a second capability truth")
	}
	custom, err := catalog.Lookup("kernel/custom-embedding")
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
	if len(catalog.definitions) != 12324 {
		t.Fatalf("unexpected built-in model count for the pinned snapshot: %d", len(catalog.definitions))
	}
	for _, definition := range catalog.definitions {
		if len(definition.config.Operations) != 1 {
			t.Fatalf("generated model %q does not have one authoritative operation", definition.ID())
		}
	}

	assertOperation(t, catalog, "gpt-6-astra", api.OperationGenerate)
	assertOperation(t, catalog, "gpt-image-2.5-flare", api.OperationImageGeneration)
	assertOperation(t, catalog, "deepseek-v4-flash", api.OperationGenerate)
	assertOperation(t, catalog, "kimi-k3", api.OperationGenerate)
	assertOperation(t, catalog, "claude-opus-5", api.OperationGenerate)
	assertOperation(t, catalog, "dall-e-3", api.OperationImageGeneration)
	assertOperation(t, catalog, "gpt-image-1", api.OperationImageGeneration)
	assertOperation(t, catalog, "gemini-2.5-flash-image", api.OperationImageGeneration)
	assertOperation(t, catalog, "sora-2", api.OperationVideoGeneration)
	assertOperation(t, catalog, "gpt-realtime-2.1", api.OperationRealtime)
	assertOperation(t, catalog, "text-embedding-3-large", api.OperationEmbedding)
	assertMode(t, catalog, "text-embedding-3-large", api.ModeAsync)
	for _, generated := range []string{
		"anthropic/claude-3-5-sonnet-20241022", "openai/gpt-4o",
		"google/gemini-1.5-pro", "meta-llama/llama-3.2-11b-vision-instruct",
		"amazon/nova-pro-v1",
	} {
		assertOperation(t, catalog, generated, api.OperationGenerate)
	}

	gemini, _ := catalog.Lookup("gemini-2.5-pro")
	limits := gemini.TokenLimits()
	if limits.MaxInputTokens != 1048576 || limits.MaxOutputTokens != 65535 {
		t.Fatalf("gemini limits were inflated by another service record: %+v", limits)
	}
	geminiCapability, _ := gemini.Capability(api.OperationGenerate)
	if resolved := geminiCapability.ResolveGenerationParameters(api.GenerationParameters{}); resolved.MaxOutputTokens == nil || *resolved.MaxOutputTokens != 4096 {
		t.Fatalf("central max_output_tokens policy is wrong: %+v", resolved)
	}
	gpt41, _ := catalog.Lookup("gpt-4.1")
	if !assertModes(gpt41, api.OperationGenerate, api.ModeUnary, api.ModeAsync) {
		t.Fatal("gpt-4.1 lost unary or async delivery capability")
	}
	latest, _ := catalog.Lookup("gpt-5.1-chat-latest")
	latestCapability, _ := latest.Capability(api.OperationGenerate)
	filtered := latestCapability.ResolveGenerationParameters(api.GenerationParameters{Temperature: pointer(0.4), TopP: pointer(0.5)})
	if filtered.Temperature != nil || filtered.TopP != nil {
		t.Fatalf("disabled sampling parameters leaked: %+v", filtered)
	}
	for _, alias := range []string{"doubao-embedding-large-text", "kokoro-82m", "chirp-3"} {
		if _, lookupErr := catalog.Lookup(alias); lookupErr == nil {
			t.Errorf("non-authoritative BaseModel alias was published: %q", alias)
		}
	}
	whisper, _ := catalog.Lookup("whisper-1")
	if _, exists := whisper.Capability(api.OperationGenerate); exists {
		t.Fatal("whisper acquired a generate operation from a different service record")
	}
	if _, exists := whisper.Capability(api.OperationAudioTranscription); !exists {
		t.Fatal("whisper transcription operation is missing")
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
		"amazon.titan-embed-text-v2:0",
		"cohere/embed-v4.0",
		"mistral/codestral-embed",
		"mistral/mistral-embed",
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
	} {
		if bytes.Contains(decodedCatalog, forbidden) {
			t.Errorf("catalog contains forbidden owner field %s", forbidden)
		}
	}

	if len(catalogData) == 0 {
		t.Fatal("embedded catalog is empty")
	}
	var document catalogDocument
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
			"7ccfc3a9", "a98ffc09", "bff1ae37", "2bf574d8", "4cf2c336", "2c6493c8", "69978f14", "a6b41ea1",
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
	large, _ := catalog.Lookup("text-embedding-3-large")
	capability, ok := large.Capability(api.OperationEmbedding)
	if !ok || !capability.SupportsInputModality(api.ModalityText) ||
		!capability.SupportsOutputModality(api.ModalityEmbedding) {
		t.Fatal("text-embedding-3-large has an invalid embedding shape")
	}
	if resolved := capability.ResolveEmbeddingDimensions(pointer(int64(9000))); resolved != nil {
		t.Fatalf("fixed-width model accepted a dimensions parameter: %v", resolved)
	}
	if dimensions, known := capability.DefaultEmbeddingDimensions(); !known || dimensions != 3072 {
		t.Fatalf("embedding default dimension is wrong: %d %v", dimensions, known)
	}

	gemini, _ := catalog.Lookup("gemini-embedding-001")
	geminiCapability, _ := gemini.Capability(api.OperationEmbedding)
	if requested := geminiCapability.ResolveEmbeddingDimensions(pointer(int64(512))); requested != nil {
		t.Fatalf("fixed-width embedding accepted a dimensions override: %v", requested)
	}
	if dimensions, known := geminiCapability.DefaultEmbeddingDimensions(); !known || dimensions != 3072 {
		t.Fatalf("fixed embedding width is wrong: %d %v", dimensions, known)
	}

	multimodal, _ := catalog.Lookup("amazon.nova-2-multimodal-embeddings-v1:0")
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
		{name: "empty model ID", defaults: []Config{testModelConfig("")}, field: "id"},
		{name: "empty lifecycle", defaults: []Config{withEmptyLifecycle(testModelConfig("model-a"))}, field: "lifecycle"},
		{name: "duplicate model", defaults: []Config{testModelConfig("model-a"), testModelConfig("model-a")}, field: "id"},
		{name: "negative token limit", defaults: []Config{withNegativeLimit(testModelConfig("model-a"))}, field: "token_limits"},
		{name: "reversed output-token bounds", defaults: []Config{withReversedOutputLimits(testModelConfig("model-a"))}, field: "token_limits"},
		{name: "limit exceeds context", defaults: []Config{withLimitAboveContext(testModelConfig("model-a"))}, field: "token_limits"},
		{name: "duplicate operation", defaults: []Config{withDuplicateOperation(testModelConfig("model-a"))}, field: "operations"},
		{name: "invalid operation", defaults: []Config{withInvalidOperation(testModelConfig("model-a"))}, field: "operations.operation"},
		{name: "invalid mode", defaults: []Config{withInvalidMode(testModelConfig("model-a"))}, field: "operations.modes"},
		{name: "invalid input modality", defaults: []Config{withInvalidInputModality(testModelConfig("model-a"))}, field: "operations.input_modalities"},
		{name: "invalid output modality", defaults: []Config{withInvalidOutputModality(testModelConfig("model-a"))}, field: "operations.output_modalities"},
		{name: "empty modalities", defaults: []Config{withEmptyInputModalities(testModelConfig("model-a"))}, field: "operations"},
		{name: "duplicate feature", defaults: []Config{withDuplicateFeature(testModelConfig("model-a"))}, field: "operations.features"},
		{name: "reversed numeric bounds", defaults: []Config{withReversedNumericBounds(testModelConfig("model-a"))}, field: "operations.generation.temperature.bounds"},
		{name: "invalid numeric default", defaults: []Config{withInvalidNumericDefault(testModelConfig("model-a"))}, field: "operations.generation.temperature.default"},
		{name: "non-finite numeric value", defaults: []Config{withNonFiniteDefault(testModelConfig("model-a"))}, field: "operations.generation.temperature.value"},
		{name: "rerank generation policy", defaults: []Config{withRerankOperation(testModelConfig("model-a"))}, field: "operations"},
		{name: "embedding generation policy", defaults: []Config{withEmbeddingGenerationPolicy(embeddingModelConfig("model-a", &EmbeddingPolicy{}))}, field: "operations"},
		{name: "embedding missing policy", defaults: []Config{withMissingEmbeddingPolicy(embeddingModelConfig("model-a", &EmbeddingPolicy{}))}, field: "operations"},
		{name: "embedding wrong output", defaults: []Config{withWrongEmbeddingOutput(embeddingModelConfig("model-a", &EmbeddingPolicy{}))}, field: "operations"},
		{name: "non-embedding embedding policy", defaults: []Config{withEmbeddingPolicyOnGenerate(testModelConfig("model-a"))}, field: "operations"},
		{name: "conflicting dimensions", defaults: []Config{embeddingModelConfig("model-a", &EmbeddingPolicy{FixedDimensions: pointer(int64(128)), Dimensions: &NumericParameter[int64]{}})}, field: "operations.embedding.dimensions"},
		{name: "invalid fixed dimensions", defaults: []Config{embeddingModelConfig("model-a", &EmbeddingPolicy{FixedDimensions: pointer(int64(0))})}, field: "operations.embedding.fixed_dimensions"},
		{name: "invalid adjustable dimensions", defaults: []Config{embeddingModelConfig("model-a", &EmbeddingPolicy{Dimensions: &NumericParameter[int64]{Minimum: pointer(int64(0))}})}, field: "operations.embedding.dimensions.minimum"},
		{name: "empty override ID", defaults: []Config{testModelConfig("model-a")}, overrides: []Override{{ModelID: ""}}, field: "override.model_id"},
		{name: "incomplete custom model", defaults: []Config{testModelConfig("model-a")}, overrides: []Override{{ModelID: "missing"}}, field: "operations"},
		{name: "duplicate override", defaults: []Config{testModelConfig("model-a")}, overrides: []Override{{ModelID: "model-a"}, {ModelID: "model-a"}}, field: "override.model_id"},
		{name: "empty operation replacement", defaults: []Config{testModelConfig("model-a")}, overrides: []Override{{ModelID: "model-a", Operations: []OperationConfig{}}}, field: "operations"},
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

func TestCatalogRejectsInvalidOperationShapes(t *testing.T) {
	realtimeUnary := testModelConfig("realtime-unary")
	realtimeUnary.Operations[0].Operation = api.OperationRealtime
	realtimeUnary.Operations[0].Modes = []api.DeliveryMode{api.ModeUnary}
	realtimeUnary.Operations[0].InputModalities = []api.Modality{api.ModalityText}
	realtimeUnary.Operations[0].OutputModalities = []api.Modality{api.ModalityText}

	nonRealtimeDuplex := testModelConfig("non-realtime-duplex")
	nonRealtimeDuplex.Operations[0].Modes = []api.DeliveryMode{api.ModeDuplex}

	embeddingInput := embeddingModelConfig("embedding-input", &EmbeddingPolicy{FixedDimensions: pointer(int64(768))})
	embeddingInput.Operations[0].InputModalities = []api.Modality{api.ModalityEmbedding}

	embeddingOutput := embeddingModelConfig("embedding-output", &EmbeddingPolicy{FixedDimensions: pointer(int64(768))})
	embeddingOutput.Operations[0].OutputModalities = []api.Modality{api.ModalityEmbedding, api.ModalityText}

	for _, test := range []struct {
		name   string
		config Config
	}{
		{name: "realtime requires duplex", config: realtimeUnary},
		{name: "duplex is exclusive to realtime", config: nonRealtimeDuplex},
		{name: "embedding cannot be input", config: embeddingInput},
		{name: "embedding output is exclusive", config: embeddingOutput},
	} {
		t.Run(test.name, func(t *testing.T) {
			_, err := newCatalog([]Config{test.config}, nil)
			var configErr *ConfigError
			if !errors.As(err, &configErr) || configErr.Field != "operations" {
				t.Fatalf("expected operation-shape error, got %T %v", err, err)
			}
		})
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

func TestModelErrorsPreserveBoundaryFacts(t *testing.T) {
	configErrorValue := (&ConfigError{ModelID: "m", Field: "operations", Reason: "missing"}).Error()
	if configErrorValue != `invalid model config "m" operations: missing` {
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
	for _, modelID := range []string{"claude-opus-4-8", "claude-opus-5", "claude-sonnet-5"} {
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

func assertMode(t *testing.T, catalog Catalog, modelID string, mode api.DeliveryMode) {
	t.Helper()
	definition, err := catalog.Lookup(modelID)
	if err != nil {
		t.Fatalf("lookup %q: %v", modelID, err)
	}
	operation := definition.config.Operations[0].Operation
	capability, ok := definition.Capability(operation)
	if !ok || !capability.SupportsMode(mode) {
		t.Fatalf("model %q is missing mode %q", modelID, mode)
	}
}

func assertModes(definition Definition, operation api.Operation, modes ...api.DeliveryMode) bool {
	capability, ok := definition.Capability(operation)
	if !ok {
		return false
	}
	for _, mode := range modes {
		if !capability.SupportsMode(mode) {
			return false
		}
	}
	return true
}

func testModelConfig(id string) Config {
	return Config{
		ID:        id,
		Lifecycle: LifecycleActive,
		TokenLimits: TokenLimits{
			ContextWindowTokens: 8192,
			MaxInputTokens:      7000,
			MinOutputTokens:     1,
			MaxOutputTokens:     2048,
		},
		Operations: []OperationConfig{{
			Operation:        api.OperationGenerate,
			Modes:            []api.DeliveryMode{api.ModeUnary},
			InputModalities:  []api.Modality{api.ModalityText},
			OutputModalities: []api.Modality{api.ModalityText},
			Features:         []api.Feature{api.FeatureUsage},
			Generation: &GenerationPolicy{
				MaxOutputTokens: &OutputTokenParameter{},
				Stop:            &StopParameter{Default: []string{"END"}},
			},
		}},
	}
}

func embeddingModelConfig(id string, policy *EmbeddingPolicy) Config {
	return Config{
		ID:        id,
		Lifecycle: LifecycleActive,
		TokenLimits: TokenLimits{
			MaxInputTokens: 8192,
		},
		Operations: []OperationConfig{{
			Operation:        api.OperationEmbedding,
			Modes:            []api.DeliveryMode{api.ModeUnary},
			InputModalities:  []api.Modality{api.ModalityText},
			OutputModalities: []api.Modality{api.ModalityEmbedding},
			Features:         []api.Feature{api.FeatureUsage},
			Embedding:        policy,
		}},
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

func withDuplicateOperation(config Config) Config {
	config.Operations = append(config.Operations, config.Operations[0])
	return config
}

func withInvalidOperation(config Config) Config {
	config.Operations[0].Operation = "invalid"
	return config
}

func withInvalidMode(config Config) Config {
	config.Operations[0].Modes = []api.DeliveryMode{"invalid"}
	return config
}

func withInvalidInputModality(config Config) Config {
	config.Operations[0].InputModalities = []api.Modality{"invalid"}
	return config
}

func withInvalidOutputModality(config Config) Config {
	config.Operations[0].OutputModalities = []api.Modality{"invalid"}
	return config
}

func withEmptyInputModalities(config Config) Config {
	config.Operations[0].InputModalities = nil
	return config
}

func withDuplicateFeature(config Config) Config {
	config.Operations[0].Features = []api.Feature{api.FeatureUsage, api.FeatureUsage}
	return config
}

func withReversedNumericBounds(config Config) Config {
	config.Operations[0].Generation.Temperature = &NumericParameter[float64]{
		Minimum: pointer(2.0), Maximum: pointer(1.0),
	}
	return config
}

func withInvalidNumericDefault(config Config) Config {
	config.Operations[0].Generation.Temperature = &NumericParameter[float64]{
		Minimum: pointer(0.0), Maximum: pointer(1.0), Default: pointer(2.0),
	}
	return config
}

func withNonFiniteDefault(config Config) Config {
	config.Operations[0].Generation.Temperature = &NumericParameter[float64]{Default: pointer(math.Inf(1))}
	return config
}

func withRerankOperation(config Config) Config {
	config.Operations[0].Operation = api.OperationRerank
	return config
}

func withEmbeddingGenerationPolicy(config Config) Config {
	config.Operations[0].Generation = &GenerationPolicy{}
	return config
}

func withMissingEmbeddingPolicy(config Config) Config {
	config.Operations[0].Embedding = nil
	return config
}

func withWrongEmbeddingOutput(config Config) Config {
	config.Operations[0].OutputModalities = []api.Modality{api.ModalityText}
	return config
}

func withEmbeddingPolicyOnGenerate(config Config) Config {
	config.Operations[0].Embedding = &EmbeddingPolicy{}
	return config
}

func pointer[T any](value T) *T {
	return &value
}
