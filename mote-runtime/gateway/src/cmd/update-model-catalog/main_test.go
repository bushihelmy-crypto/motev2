package main

import (
	"encoding/json"
	"errors"
	"go/ast"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	modelcatalog "github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
)

// compileForTest exposes the first diagnostic for focused assertions. The
// production command prints diagnostics for the current generation only; it
// does not persist a rejection artifact.
func compileForTest(modelID string, matches []recordRef) (modelConfig, bool, error) {
	compiled, present, rejections := compileModelGroup(modelID, matches)
	if len(rejections) == 0 {
		return compiled, present, nil
	}
	return compiled, present, rejections[0]
}

func TestExplicitSourceModeOwnsOperationClassification(t *testing.T) {
	cases := []struct {
		name     string
		modelID  string
		mode     string
		outputs  []string
		expected string
	}{
		{name: "general model on image row keeps source operation", modelID: "anthropic/claude-3-5-sonnet-20241022", mode: "image_generation", expected: "image_generation"},
		{name: "mixed image row keeps source operation", modelID: "deep-research-pro", mode: "image_generation", outputs: []string{"text", "image"}, expected: "image_generation"},
		{name: "actual image model", modelID: "gpt-image-1", mode: "image_generation", expected: "image_generation"},
		{name: "speech model with an image-like name", modelID: "@cf/deepgram/flux", mode: "audio_transcription", expected: "audio_transcription"},
		{name: "explicit chat mode wins over image name", modelID: "gpt-image-1", mode: "chat", outputs: []string{"image"}, expected: "generate"},
		{name: "explicit chat mode wins over embedding name", modelID: "titan-embed-text-v2", mode: "chat", expected: "generate"},
		{name: "video model", modelID: "wan-2.6-t2v", mode: "video_generation", expected: "video_generation"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			ref := recordRef{record: sourceRecord{Mode: testCase.mode, SupportedOutputModalities: testCase.outputs}}
			got, reason := authoritativeOperation(ref)
			if reason != "" || string(got) != testCase.expected {
				t.Fatalf("authoritativeOperation(%q) = %q, %q, want %q", testCase.mode, got, reason, testCase.expected)
			}
		})
	}
}

func TestExplicitSourceModalitiesAreExactAndMultimodalFactsAreRetained(t *testing.T) {
	realtime := recordRef{key: "gpt-realtime-translate", record: sourceRecord{
		Mode:                      "realtime",
		SupportedModalities:       []string{"audio"},
		SupportedOutputModalities: []string{"text", "audio"},
	}}
	compiled, _, err := compileForTest(realtime.key, []recordRef{realtime})
	if err != nil {
		t.Fatal(err)
	}
	shape := compiled.Operations[0]
	if len(shape.InputModalities) != 1 || shape.InputModalities[0] != api.ModalityAudio {
		t.Fatalf("source input modalities were widened: %v", shape.InputModalities)
	}

	image := recordRef{key: "gemini-image", record: sourceRecord{
		Mode:                      "image_generation",
		SupportedModalities:       []string{"text", "image"},
		SupportedOutputModalities: []string{"text", "image"},
	}}
	compiled, _, err = compileForTest(image.key, []recordRef{image})
	if err != nil {
		t.Fatal(err)
	}
	if got := compiled.Operations[0].OutputModalities; len(got) != 2 || got[0] != api.ModalityImage || got[1] != api.ModalityText {
		t.Fatalf("explicit multimodal output facts were lost: %v", got)
	}
}

func TestEqualSourceFactsMergeAndConflictingFactsDeleteIdentity(t *testing.T) {
	primary := recordRef{key: "model", record: sourceRecord{Mode: "chat", SupportedModalities: []string{"text"}, SupportedOutputModalities: []string{"text"}}}
	matchingBatch := recordRef{key: "provider/model:batch", record: sourceRecord{Mode: "chat", SupportedModalities: []string{"text"}, SupportedOutputModalities: []string{"text"}}}
	compiled, present, err := compileForTest(primary.key, []recordRef{primary, matchingBatch})
	if err != nil || !present {
		t.Fatalf("equal source facts were not merged: present=%v err=%v", present, err)
	}
	if got := compiled.Operations[0].InputModalities; len(got) != 1 || got[0] != api.ModalityText {
		t.Fatalf("equal source facts changed input: %v", got)
	}

	missingPrimary := recordRef{key: "model-with-batch-fact", record: sourceRecord{Mode: "chat"}}
	supplemental := recordRef{key: "provider/model-with-batch-fact:batch", record: sourceRecord{Mode: "chat", SupportedModalities: []string{"text", "image"}, SupportedOutputModalities: []string{"text"}}}
	compiled, present, err = compileForTest(missingPrimary.key, []recordRef{missingPrimary, supplemental})
	if err != nil || !present {
		t.Fatalf("complementary source fact was not merged: present=%v err=%v", present, err)
	}
	if got := compiled.Operations[0].InputModalities; len(got) != 2 || got[0] != api.ModalityImage || got[1] != api.ModalityText {
		t.Fatalf("explicit supplemental input was not retained: %v", got)
	}

	conflicting := recordRef{key: "provider/model:batch", record: sourceRecord{Mode: "chat", SupportedModalities: []string{"text", "image"}, SupportedOutputModalities: []string{"text"}}}
	_, present, err = compileForTest(primary.key, []recordRef{primary, conflicting})
	var compileErr *compileError
	if present || !errors.As(err, &compileErr) || !strings.Contains(compileErr.Reason, "supported_modalities") {
		t.Fatalf("conflicting model was not rejected as a whole: present=%v %T %v", present, err, err)
	}

	missingPrimary = recordRef{key: "model-with-two-batch-facts", record: sourceRecord{Mode: "chat"}}
	firstSupplemental := recordRef{key: "provider/model-with-two-batch-facts:batch", record: sourceRecord{Mode: "chat", SupportedModalities: []string{"text"}, SupportedOutputModalities: []string{"text"}}}
	secondSupplemental := recordRef{key: "other/model-with-two-batch-facts:batch", record: sourceRecord{Mode: "chat", SupportedModalities: []string{"image"}, SupportedOutputModalities: []string{"text"}}}
	_, present, err = compileForTest(missingPrimary.key, []recordRef{missingPrimary, firstSupplemental, secondSupplemental})
	if !errors.As(err, &compileErr) || !strings.Contains(compileErr.Reason, "supported_modalities") {
		t.Fatalf("disagreeing source modality facts were not fatal: present=%v %T %v", present, err, err)
	}
}

func TestQualifiedOnlyIdentityRejectsConflictingModalityFacts(t *testing.T) {
	records := []recordRef{
		{key: "first/model", record: sourceRecord{Mode: "chat", SupportedModalities: []string{"text"}, SupportedOutputModalities: []string{"text"}}},
		{key: "second/model", record: sourceRecord{Mode: "chat", SupportedModalities: []string{"text", "image"}, SupportedOutputModalities: []string{"text"}}},
	}
	models, rejected := compileModels(records, nil)
	if len(models) != 0 {
		t.Fatalf("qualified observations with no authoritative owner were published: %+v", models)
	}
	if len(rejected) != 1 || !strings.Contains(rejected[0].Reason, "supported_modalities") {
		t.Fatalf("qualified modality conflict was not rejected once: %v", rejected)
	}
}

func TestPrefixCollisionDeletesTheWholeCanonicalModel(t *testing.T) {
	records := []recordRef{
		{key: "model", record: sourceRecord{Mode: "chat", SupportedModalities: []string{"text"}, SupportedOutputModalities: []string{"text"}}},
		{key: "provider/model", record: sourceRecord{Mode: "chat", SupportedModalities: []string{"text", "image"}, SupportedOutputModalities: []string{"text"}}},
	}
	models, rejected := compileModels(records, nil)
	if len(models) != 0 {
		t.Fatalf("conflicting prefixed and bare records left a partial model: %+v", models)
	}
	if len(rejected) == 0 || !strings.Contains(rejected[0].Reason, "supported_modalities") {
		t.Fatalf("prefix collision was not diagnosed: %v", rejected)
	}
}

func TestPeerParameterConflictDeletesTheWholeCanonicalModel(t *testing.T) {
	first := recordRef{key: "model", record: sourceRecord{Mode: "chat", ModelParameters: []sourceParameter{{ID: "temperature", Default: json.RawMessage("1"), Range: &sourceRange{Minimum: numberPtr("0"), Maximum: numberPtr("2")}}}}}
	second := recordRef{key: "provider/model", record: sourceRecord{Mode: "chat", ModelParameters: []sourceParameter{{ID: "temperature", Default: json.RawMessage("0.7"), Range: &sourceRange{Minimum: numberPtr("0"), Maximum: numberPtr("1")}}}}}
	models, rejected := compileModels([]recordRef{first, second}, nil)
	if len(models) != 0 || len(rejected) == 0 {
		t.Fatalf("conflicting peer parameter facts were published: models=%v rejected=%v", models, rejected)
	}
}

func TestSupplementalOperationConflictIsRejected(t *testing.T) {
	primary := recordRef{key: "model", record: sourceRecord{Mode: "chat"}}
	batch := recordRef{key: "provider/model:batch", record: sourceRecord{Mode: "embedding"}}
	compiled, present, err := compileForTest(primary.key, []recordRef{primary, batch})
	var compileErr *compileError
	if present || len(compiled.Operations) != 0 {
		t.Fatalf("conflicting operation left a partial model: present=%v model=%+v", present, compiled)
	}
	if !errors.As(err, &compileErr) || !strings.Contains(compileErr.Reason, "conflicting operations") {
		t.Fatalf("canonical operation conflict was not rejected: %T %v", err, err)
	}
}

func TestCanonicalIdentityRemovesEveryPrefixAndDeduplicatesByLeaf(t *testing.T) {
	records := []recordRef{
		{key: "gpt-4.1", record: sourceRecord{Mode: "chat"}},
		{key: "openai/gpt-4.1", record: sourceRecord{Mode: "image_generation", Source: "merged_from_llm_models_csv"}},
		{key: "mistral/codestral-embed", record: sourceRecord{Mode: "embedding"}},
		{key: "vercel_ai_gateway/mistral/codestral-embed", record: sourceRecord{Mode: "chat"}},
	}
	models, rejected := compileModels(records, nil)
	if len(rejected) != 1 || !strings.Contains(rejected[0].Reason, "conflicting operations") {
		t.Fatalf("conflicting operation identity was not rejected: %v", rejected)
	}
	seen := make(map[string]modelConfig, len(models))
	for _, model := range models {
		seen[model.ID] = model
	}
	for modelID := range seen {
		if strings.Contains(modelID, "/") {
			t.Fatalf("qualified prefix leaked into canonical model ID %q", modelID)
		}
	}
	if _, ok := seen["gpt-4.1"]; !ok {
		t.Fatal("bare canonical model was lost")
	}
	if _, ok := seen["codestral-embed"]; ok {
		t.Fatalf("same leaf with conflicting operations was published: %+v", seen["codestral-embed"])
	}
}

func TestCanonicalIdentityAlwaysUsesFinalPathSegment(t *testing.T) {
	records := []recordRef{
		{key: "openrouter/cohere/command", record: sourceRecord{BaseModel: "cohere/command", Provider: "litellm", Mode: "chat"}},
		{key: "vercel_ai_gateway/mistral/codestral", record: sourceRecord{BaseModel: "codestral", Provider: "vercel_ai_gateway", Mode: "chat"}},
		{key: "vercel_ai_gateway/mistral/devstral-small", record: sourceRecord{BaseModel: "devstral-small", Provider: "vercel_ai_gateway", Mode: "chat"}},
		{key: "openrouter/openai/gpt-5", record: sourceRecord{BaseModel: "gpt-5", Provider: "openrouter", Mode: "chat"}},
		{key: "DeepSeek-V4-Flash", record: sourceRecord{Mode: "chat"}},
	}
	newModels := map[string]struct{}{"command": {}, "codestral": {}, "deepseek-v4-flash": {}, "devstral-small": {}, "gpt-5": {}}
	index := buildIdentityIndex(records, newModels)
	cases := []struct {
		key      string
		expected string
	}{
		{key: "openrouter/cohere/command", expected: "command"},
		{key: "vercel_ai_gateway/mistral/codestral", expected: "codestral"},
		{key: "vercel_ai_gateway/mistral/devstral-small", expected: "devstral-small"},
		{key: "openrouter/openai/gpt-5", expected: "gpt-5"},
		{key: "DeepSeek-V4-Flash", expected: "deepseek-v4-flash"},
	}
	for _, testCase := range cases {
		t.Run(testCase.key, func(t *testing.T) {
			ref := records[0]
			for _, candidate := range records {
				if candidate.key == testCase.key {
					ref = candidate
					break
				}
			}
			if got := canonicalRecordID(ref, index); got != testCase.expected {
				t.Fatalf("canonicalRecordID(%q) = %q, want %q", testCase.key, got, testCase.expected)
			}
		})
	}
}

func TestMalformedQualifiedAndBatchSourcesBlockInference(t *testing.T) {
	cases := []recordRef{
		{key: "apiserpent/deep-search", record: sourceRecord{Provider: "apiserpent", Mode: "search"}},
		{key: "vertex_ai/deepseek-ocr:batch", record: sourceRecord{Provider: "vertex_ai", Mode: "ocr"}},
		{key: "azure/guardrails", record: sourceRecord{Provider: "azure"}},
	}
	for _, source := range cases {
		t.Run(source.key, func(t *testing.T) {
			models, rejected := compileModels([]recordRef{source}, nil)
			if len(models) != 0 {
				t.Fatalf("malformed source was published through inference: %+v", models)
			}
			if len(rejected) != 1 {
				t.Fatalf("malformed source rejection count = %d, want 1: %v", len(rejected), rejected)
			}
		})
	}
}

func TestCanonicalModelDropsConflictingOperations(t *testing.T) {
	records := []recordRef{
		{key: "model", record: sourceRecord{Mode: "chat"}},
		{key: "openrouter/model", record: sourceRecord{BaseModel: "model", Mode: "audio_speech"}},
		{key: "openrouter/model:batch", record: sourceRecord{BaseModel: "model:batch", Mode: "chat"}},
		{key: "vercel_ai_gateway/model:batch", record: sourceRecord{BaseModel: "model:batch", Mode: "embedding"}},
	}
	models, rejected := compileModels(records, nil)
	if len(models) != 0 {
		t.Fatalf("conflicting canonical operations were published: %v", models)
	}
	if len(rejected) != 1 || !strings.Contains(rejected[0].Reason, "conflicting operations") {
		t.Fatalf("conflicting operation was not rejected: %v", rejected)
	}
}

func TestUnknownSourceModalityIsRejected(t *testing.T) {
	primary := recordRef{key: "code-model", record: sourceRecord{
		Mode:                      "chat",
		SupportedModalities:       []string{"text"},
		SupportedOutputModalities: []string{"text", "code"},
	}}
	_, _, err := compileForTest(primary.key, []recordRef{primary})
	var compileErr *compileError
	if !errors.As(err, &compileErr) || !strings.Contains(compileErr.Reason, "unsupported modality") {
		t.Fatalf("unknown source modality was not rejected: %T %v", err, err)
	}
}

func TestExactSourceRecordWithoutModeCannotFallBackToNameInference(t *testing.T) {
	primary := recordRef{key: "gpt-6-astra", record: sourceRecord{}}
	models, rejected := compileModels([]recordRef{primary}, map[string]struct{}{primary.key: {}})
	if len(models) != 0 || len(rejected) != 1 || rejected[0].Field != "mode" {
		t.Fatalf("missing authoritative mode was not rejected: models=%v rejected=%v", models, rejected)
	}
	models, rejected = compileModels([]recordRef{primary}, nil)
	if len(models) != 0 || len(rejected) != 1 || rejected[0].Field != "mode" {
		t.Fatalf("source-only missing mode was silently discarded: models=%v rejected=%v", models, rejected)
	}

	unknown := recordRef{key: "search-like-model", record: sourceRecord{Mode: "search"}}
	models, rejected = compileModels([]recordRef{unknown}, nil)
	if len(models) != 0 || len(rejected) != 1 || rejected[0].Field != "operation" {
		t.Fatalf("unknown authoritative mode was silently discarded: models=%v rejected=%v", models, rejected)
	}
	metadata := recordRef{key: "fallback_generalizations", record: sourceRecord{BaseModel: "fallback-generalizations"}}
	models, rejected = compileModels([]recordRef{metadata}, nil)
	if len(models) != 0 || len(rejected) != 0 {
		t.Fatalf("fallback metadata was treated as a model: models=%v rejected=%v", models, rejected)
	}
}

func TestSyntheticRequestPresetsAreNotModels(t *testing.T) {
	for _, modelID := range []string{
		"conservative", "creative", "edit", "erase", "fast", "inpaint", "outpaint",
		"preset/deep-research", "claude-opus-4-8-high", "deepseek-v4-flash-max",
		"o3-mini-high", "grok-3-search", "openrouter/openai/gpt-5:batch",
	} {
		if !syntheticModelID(modelID) {
			t.Errorf("syntheticModelID(%q) = false", modelID)
		}
	}
	for _, modelID := range []string{"dall-e-3", "gpt-image-1", "stable-image-core", "claude-opus-4-8"} {
		if syntheticModelID(modelID) {
			t.Errorf("syntheticModelID(%q) = true for a model", modelID)
		}
	}
}

func TestAmbiguousCanonicalLeafIsNotNameInferred(t *testing.T) {
	model, present, rejected := compileInferredModel("flux")
	if present || len(rejected) != 0 || model.ID != "" {
		t.Fatalf("ambiguous leaf acquired an invented operation: present=%v model=%+v diagnostics=%v", present, model, rejected)
	}
}

func TestResolveStringLiteralSupportsModelConstants(t *testing.T) {
	constants := map[string]string{"ModelName": "black-forest-labs/flux-1.1-pro"}
	if value, ok := resolveStringLiteral(&ast.Ident{Name: "ModelName"}, constants); !ok || value != constants["ModelName"] {
		t.Fatalf("resolved identifier = %q, %v", value, ok)
	}
	if value, ok := resolveStringLiteral(&ast.Ident{Name: "Missing"}, constants); ok || value != "" {
		t.Fatalf("missing identifier resolved as %q, %v", value, ok)
	}
}

func TestDecodeSourceRecordsIsStrict(t *testing.T) {
	valid := `{"model":{"mode":"chat"}}`
	if records, err := decodeSourceRecords([]byte(valid)); err != nil || len(records) != 1 {
		t.Fatalf("valid source snapshot failed: %v (%v)", records, err)
	}
	for _, input := range []string{"", "null", "{}", `{"model":null}`, `{"model":{}} {}`, `{"model":{}} garbage`, `{"model":{},"model":{}}`, `[]`} {
		if _, err := decodeSourceRecords([]byte(input)); err == nil {
			t.Errorf("decodeSourceRecords(%q) accepted malformed input", input)
		}
	}
}

func TestCompileModesRetainsPrimaryUnaryAndSupplementalAsyncOrStreaming(t *testing.T) {
	primary := recordRef{key: "gemini-2.5-pro", record: sourceRecord{
		Mode: "chat", SupportedEndpoints: []string{"/v1/chat/completions", "/v1/batch"}, SupportsNativeStreaming: true,
	}}
	batch := recordRef{key: "openrouter/google/gemini-2.5-pro:batch", record: sourceRecord{
		BaseModel: "gemini-2.5-pro:batch", Mode: "chat", SupportedEndpoints: []string{"/v1/batch"},
	}}
	models, rejected := compileModels([]recordRef{primary, batch}, nil)
	if len(rejected) != 0 || len(models) != 1 {
		t.Fatalf("compiled models = %d, rejected = %v", len(models), rejected)
	}
	compiled := models[0]
	if got := compiled.Operations[0].Modes; len(got) != 3 || got[0] != "async" || got[1] != "server_stream" || got[2] != "unary" {
		t.Fatalf("supplemental modes = %v, want async/server_stream/unary", got)
	}
	batchOnly := recordRef{key: "batch-model", record: sourceRecord{SupportedEndpoints: []string{"/v1/batch"}}}
	if got := compileModes("generate", []recordRef{batchOnly}); len(got) != 1 || got[0] != "async" {
		t.Fatalf("batch-only modes = %v, want async", got)
	}
}

func TestDisabledParametersAreNotPublished(t *testing.T) {
	primary := recordRef{key: "gpt-5.1-chat-latest", record: sourceRecord{
		Mode: "chat", MaxOutputTokens: numberPtr("16384"),
		ModelParameters: []sourceParameter{
			{ID: "temperature", Disabled: true, Default: json.RawMessage("1"), Range: &sourceRange{Minimum: numberPtr("0"), Maximum: numberPtr("2")}},
			{ID: "top_p", Disabled: true, Default: json.RawMessage("1"), Range: &sourceRange{Minimum: numberPtr("0"), Maximum: numberPtr("1")}},
			{ID: "max_completion_tokens", Range: &sourceRange{Minimum: numberPtr("1"), Maximum: numberPtr("16384")}},
		},
	}}
	compiled, _, err := compileForTest(primary.key, []recordRef{primary})
	if err != nil {
		t.Fatal(err)
	}
	policy := compiled.Operations[0].Generation
	if policy == nil || policy.Temperature != nil || policy.TopP != nil || policy.MaxOutputTokens == nil {
		t.Fatalf("disabled parameters leaked into policy: %+v", policy)
	}
}

func TestStructuredOutputModelCompatibilityIsPublished(t *testing.T) {
	primary := recordRef{key: "structured-model", record: sourceRecord{
		Mode:                    "chat",
		SupportsFunctionCalling: true,
		SupportsResponseSchema:  true,
	}}
	compiled, _, err := compileForTest(primary.key, []recordRef{primary})
	if err != nil {
		t.Fatal(err)
	}
	features := compiled.Operations[0].Features
	if len(features) != 3 || features[0] != api.FeatureStructured || features[1] != api.FeatureToolCalls || features[2] != api.FeatureUsage {
		t.Fatalf("model compatibility evidence was not published deterministically: %v", features)
	}
}

func TestInvalidSourceBoundsAreRejected(t *testing.T) {
	minimum, maximum := number("2"), number("1")
	primary := recordRef{key: "bad-model", record: sourceRecord{
		Mode: "chat", ModelParameters: []sourceParameter{{ID: "temperature", Range: &sourceRange{Minimum: &minimum, Maximum: &maximum}}},
	}}
	_, _, err := compileForTest(primary.key, []recordRef{primary})
	var compileErr *compileError
	if !errors.As(err, &compileErr) || compileErr.Field != "operation" || !strings.Contains(compileErr.Reason, "temperature") {
		t.Fatalf("invalid source bounds were not rejected: %T %v", err, err)
	}
}

func TestOutputTokenUIDefaultDoesNotBecomeGatewayPolicy(t *testing.T) {
	primary := recordRef{key: "model", record: sourceRecord{
		Mode: "chat", ModelParameters: []sourceParameter{{
			ID: "max_tokens", Default: json.RawMessage("32768"),
			Range: &sourceRange{Minimum: numberPtr("1"), Maximum: numberPtr("8192")},
		}},
	}}
	compiled, _, err := compileForTest(primary.key, []recordRef{primary})
	if err != nil {
		t.Fatalf("unpublished UI default rejected the model: %v", err)
	}
	if compiled.TokenLimits.MinOutputTokens != 1 || compiled.TokenLimits.MaxOutputTokens != 8192 {
		t.Fatalf("declared output bounds were not retained: %+v", compiled.TokenLimits)
	}
	encoded, encodeErr := json.Marshal(compiled.Operations[0].Generation.MaxOutputTokens)
	if encodeErr != nil || string(encoded) != `{}` {
		t.Fatalf("source UI default became Gateway policy: %s (%v)", encoded, encodeErr)
	}
}

func TestInvalidEmbeddingDimensionsAreRejectedBeforeCatalogWrite(t *testing.T) {
	primary := recordRef{key: "bad-dimensions", record: sourceRecord{
		Mode: "embedding", ModelParameters: []sourceParameter{{ID: "dimensions", Range: &sourceRange{Minimum: numberPtr("0")}}},
	}}
	_, _, err := compileForTest(primary.key, []recordRef{primary})
	var compileErr *compileError
	if !errors.As(err, &compileErr) || compileErr.Field != "operation" || !strings.Contains(compileErr.Reason, "dimensions") {
		t.Fatalf("invalid embedding dimension bounds were not rejected: %T %v", err, err)
	}
}

func TestCompilationOnlyConsumesFactsOwnedByTheOperation(t *testing.T) {
	primary := recordRef{key: "1024-x-1024/50-steps/stability.stable-diffusion-xl-v1", record: sourceRecord{
		Mode: "image_generation", MaxInputTokens: numberPtr("77"), MaxTokens: numberPtr("77"),
		ModelParameters: []sourceParameter{
			{ID: "temperature", Default: json.RawMessage(`"not-a-number"`)},
			{ID: "max_tokens", Default: json.RawMessage("2048"), Range: &sourceRange{Minimum: numberPtr("1"), Maximum: numberPtr("77")}},
			{ID: "dimensions", Range: &sourceRange{Minimum: numberPtr("0")}},
		},
	}}
	compiled, _, err := compileForTest(primary.key, []recordRef{primary})
	if err != nil {
		t.Fatalf("unconsumed generic UI parameters rejected an image operation: %v", err)
	}
	if compiled.TokenLimits.MaxInputTokens != 77 || compiled.TokenLimits.MaxOutputTokens != 0 {
		t.Fatalf("image operation consumed generation-only token facts: %+v", compiled.TokenLimits)
	}
	if compiled.Operations[0].Generation != nil || compiled.Operations[0].Embedding != nil {
		t.Fatalf("image operation published an unrelated parameter policy: %+v", compiled.Operations[0])
	}
}

func TestCompileModelsRejectsOnlyTheInvalidModel(t *testing.T) {
	records := []recordRef{
		{key: "good-model", record: sourceRecord{Mode: "chat", MaxOutputTokens: numberPtr("4096")}},
		{key: "bad-model", record: sourceRecord{
			Mode: "chat", ModelParameters: []sourceParameter{{
				ID: "max_tokens", Range: &sourceRange{Minimum: numberPtr("1"), Maximum: numberPtr("0")},
			}},
		}},
	}
	models, rejected := compileModels(records, nil)
	if len(models) != 1 || models[0].ID != "good-model" {
		t.Fatalf("valid model was not the sole published model: %+v", models)
	}
	if len(rejected) != 1 || rejected[0] == nil {
		t.Fatalf("invalid model rejection was not reported: %v", rejected)
	}
}

func TestCompiledOperationShapeRejectsIllegalCombinations(t *testing.T) {
	cases := []operationConfig{
		{Operation: api.OperationRealtime, Modes: []api.DeliveryMode{api.ModeUnary}, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityText}},
		{Operation: api.OperationGenerate, Modes: []api.DeliveryMode{api.ModeDuplex}, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityText}},
		{Operation: api.OperationGenerate, Modes: []api.DeliveryMode{api.ModeUnary}, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityEmbedding}},
		{Operation: api.OperationEmbedding, Modes: []api.DeliveryMode{api.ModeUnary}, InputModalities: []api.Modality{api.ModalityEmbedding}, OutputModalities: []api.Modality{api.ModalityEmbedding}, Embedding: &embeddingPolicy{}},
	}
	for _, operation := range cases {
		if err := modelcatalog.ValidateOperationShape(modelcatalog.OperationShape{
			Operation:        operation.Operation,
			Modes:            operation.Modes,
			InputModalities:  operation.InputModalities,
			OutputModalities: operation.OutputModalities,
			HasGeneration:    operation.Generation != nil,
			HasEmbedding:     operation.Embedding != nil,
		}); err == nil {
			t.Errorf("ValidateOperationShape accepted illegal operation: %+v", operation)
		}
	}
}

func TestEquivalentParameterAliasesAreDeterministicAndConflictsFailClosed(t *testing.T) {
	equivalent, found, err := findParameter([]recordRef{{key: "a", record: sourceRecord{ModelParameters: []sourceParameter{{ID: "top_p", Default: json.RawMessage("1")}, {ID: "topp", Default: json.RawMessage("1")}}}}}, "top_p", "topp")
	if err != nil || !found || equivalent.ID != "top_p" {
		t.Fatalf("equivalent parameter aliases were not selected deterministically: %+v %v %v", equivalent, found, err)
	}
	_, found, err = findParameter([]recordRef{{key: "a", record: sourceRecord{ModelParameters: []sourceParameter{{ID: "top_p", Default: json.RawMessage("1")}, {ID: "topp", Default: json.RawMessage("0.5")}}}}}, "top_p", "topp")
	if err == nil || found {
		t.Fatalf("conflicting parameter aliases were accepted: found=%v err=%v", found, err)
	}
	outputAliases := []recordRef{{key: "model", record: sourceRecord{ModelParameters: []sourceParameter{
		{ID: "max_tokens", Default: json.RawMessage("128000"), Range: &sourceRange{Minimum: numberPtr("1"), Maximum: numberPtr("128000")}},
		{ID: "max_completion_tokens", Default: json.RawMessage("65536"), Range: &sourceRange{Minimum: numberPtr("1"), Maximum: numberPtr("128000")}},
	}}}}
	if _, found, err = outputTokenBounds(outputAliases); err != nil || !found {
		t.Fatalf("equivalent output bounds conflicted because of an unconsumed UI default: found=%v err=%v", found, err)
	}
}

func TestNoSyntheticMinimumOrCatalogDefaultIsWritten(t *testing.T) {
	primary := recordRef{key: "model", record: sourceRecord{
		Mode: "chat", MaxOutputTokens: numberPtr("2048"),
		ModelParameters: []sourceParameter{{ID: "max_tokens", Range: &sourceRange{Maximum: numberPtr("2048")}}},
	}}
	compiled, _, err := compileForTest(primary.key, []recordRef{primary})
	if err != nil {
		t.Fatal(err)
	}
	if compiled.TokenLimits.MinOutputTokens != 0 {
		t.Fatalf("minimum was invented: %+v", compiled.TokenLimits)
	}
	if compiled.Operations[0].Generation.MaxOutputTokens == nil {
		t.Fatal("max_output_tokens support was lost")
	}
	encoded, encodeErr := json.Marshal(compiled.Operations[0].Generation.MaxOutputTokens)
	if encodeErr != nil || string(encoded) != `{}` {
		t.Fatalf("catalog default leaked into output: %s (%v)", encoded, encodeErr)
	}
}

func TestConflictingOutputMaximumFactsAreRejected(t *testing.T) {
	primary := recordRef{key: "conflicting-output", record: sourceRecord{
		Mode:            "chat",
		MaxOutputTokens: numberPtr("40960"),
		ModelParameters: []sourceParameter{{ID: "max_tokens", Range: &sourceRange{Maximum: numberPtr("2048")}}},
	}}
	_, _, err := compileForTest(primary.key, []recordRef{primary})
	var compileErr *compileError
	if !errors.As(err, &compileErr) || !strings.Contains(compileErr.Reason, "conflicts with parameter range maximum") {
		t.Fatalf("conflicting output maxima were accepted: %T %v", err, err)
	}
}

func TestCatalogEncodingRejectsQualifiedModelIDs(t *testing.T) {
	_, err := encodeCatalog(catalogDocument{
		SchemaVersion: 1,
		Models:        []modelConfig{{ID: "provider/model"}},
	})
	if err == nil || !strings.Contains(err.Error(), "final path segment") {
		t.Fatalf("qualified model ID was accepted by the artifact boundary: %v", err)
	}
}

func TestCatalogArtifactPublishesAtomically(t *testing.T) {
	directory := t.TempDir()
	catalogPath := filepath.Join(directory, "catalog.json.gz")
	if err := os.WriteFile(catalogPath, []byte("old catalog"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := publishArtifact(catalogPath, []byte("new catalog")); err != nil {
		t.Fatal(err)
	}
	assertFileContent(t, catalogPath, "new catalog")
	entries, err := os.ReadDir(directory)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 {
		t.Fatalf("publication left staging files: %v", entries)
	}
}

func assertFileContent(t *testing.T, path, expected string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != expected {
		t.Fatalf("%s = %q, want %q", path, data, expected)
	}
}

func TestExplicitBatchVariantDoesNotBecomeBaseModelAlias(t *testing.T) {
	primary := recordRef{key: "doubao-embedding-large-text-240915", record: sourceRecord{
		BaseModel: "doubao-embedding-large-text", Mode: "embedding", OutputVectorSize: numberPtr("4096"),
	}}
	other := recordRef{key: "doubao-embedding-large-text-250515", record: sourceRecord{
		BaseModel: "doubao-embedding-large-text", Mode: "embedding", OutputVectorSize: numberPtr("2048"),
	}}
	compiled, rejected := compileModels([]recordRef{primary, other}, nil)
	if len(rejected) != 0 {
		t.Fatal(rejected)
	}
	for _, model := range compiled {
		if model.ID == "doubao-embedding-large-text" {
			t.Fatal("BaseModel was promoted to an alias")
		}
	}
}

func TestEqualRecordsCombineComplementaryDeliveryEvidence(t *testing.T) {
	bare := recordRef{key: "model", record: sourceRecord{Mode: "chat", SupportsNativeStreaming: true}}
	qualified := recordRef{key: "provider/model", record: sourceRecord{Mode: "chat", SupportsNativeStreaming: false}}
	compiled, rejected := compileModels([]recordRef{bare, qualified}, nil)
	if len(rejected) != 0 {
		t.Fatal(rejected)
	}
	for _, model := range compiled {
		if model.ID != "model" {
			continue
		}
		modes := model.Operations[0].Modes
		if len(modes) != 2 || modes[0] != "server_stream" || modes[1] != "unary" {
			t.Fatalf("complementary delivery evidence was not combined: %v", modes)
		}
		return
	}
	t.Fatal("canonical model was not compiled")
}

func number(value string) json.Number { return json.Number(value) }

func numberPtr(value string) *json.Number {
	result := json.Number(value)
	return &result
}
