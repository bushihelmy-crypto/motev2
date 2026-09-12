package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	modelcatalog "github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
)

// compileForTest supplies the identity that production grouping has already
// checked. The focused tests can then vary one capability fact at a time.
func compileForTest(modelID string, matches []recordRef) (modelcatalog.Config, bool, error) {
	for index := range matches {
		if matches[index].record.BaseModel == "" {
			matches[index].record.BaseModel = modelID
		}
	}
	compiled, present, rejections := compileModelGroup(modelID, matches)
	if len(rejections) == 0 {
		return compiled, present, nil
	}
	return compiled, present, rejections[0]
}

func TestExplicitSourceModeOwnsOperationClassification(t *testing.T) {
	cases := []struct {
		mode     string
		expected api.Operation
	}{
		{mode: "chat", expected: api.OperationGenerate},
		{mode: "completion", expected: api.OperationGenerate},
		{mode: "responses", expected: api.OperationGenerate},
		{mode: "embedding", expected: api.OperationEmbedding},
		{mode: "rerank", expected: api.OperationRerank},
		{mode: "image_generation", expected: api.OperationImageGeneration},
		{mode: "image_edit", expected: api.OperationImageGeneration},
		{mode: "audio_speech", expected: api.OperationAudioGeneration},
		{mode: "audio_transcription", expected: api.OperationAudioTranscription},
		{mode: "video_generation", expected: api.OperationVideoGeneration},
		{mode: "realtime", expected: api.OperationRealtime},
	}
	for _, testCase := range cases {
		t.Run(testCase.mode, func(t *testing.T) {
			got, reason := authoritativeOperation(recordRef{record: sourceRecord{Mode: testCase.mode}})
			if reason != "" || got != testCase.expected {
				t.Fatalf("authoritativeOperation(%q) = %q, %q; want %q", testCase.mode, got, reason, testCase.expected)
			}
		})
	}
}

func TestBaseModelIsTheOnlyIdentityAndSourceKeysAreNotPublished(t *testing.T) {
	records := []recordRef{
		{key: "openrouter/openai/gpt-5", record: sourceRecord{BaseModel: "gpt-5", Mode: "chat"}},
		{key: "azure/gpt-5", record: sourceRecord{BaseModel: "gpt-5", Mode: "chat"}},
	}
	models, rejected := compileModels(records)
	if len(rejected) != 0 || len(models) != 1 || models[0].BaseModel != "gpt-5" {
		t.Fatalf("source key was used as identity: models=%v rejected=%v", models, rejected)
	}

	bad := []recordRef{
		{key: "provider/no-base", record: sourceRecord{Mode: "chat"}},
		{key: "provider/qualified", record: sourceRecord{BaseModel: "provider/model", Mode: "chat"}},
	}
	models, rejected = compileModels(bad)
	if len(models) != 0 || len(rejected) != 2 {
		t.Fatalf("invalid BaseModel rows were published: models=%v rejected=%v", models, rejected)
	}
	for _, diagnostic := range rejected {
		if diagnostic.Field != "base_model" {
			t.Fatalf("invalid identity had wrong diagnostic: %+v", diagnostic)
		}
	}
}

func TestSameBaseModelFactsDeduplicateAndBatchRowsStayOut(t *testing.T) {
	first := recordRef{key: "model", record: sourceRecord{BaseModel: "model", Mode: "chat", SupportedModalities: []string{"text"}, SupportedOutputModalities: []string{"text"}}}
	second := recordRef{key: "provider/model", record: sourceRecord{BaseModel: "model", Mode: "chat", SupportedModalities: []string{"text"}, SupportedOutputModalities: []string{"text"}}}
	batch := recordRef{key: "provider/model:batch", record: sourceRecord{BaseModel: "model", Mode: "embedding", SupportedModalities: []string{"text"}, SupportedOutputModalities: []string{"embedding"}}}
	models, rejected := compileModels([]recordRef{first, second, batch})
	if len(rejected) != 0 || len(models) != 1 || models[0].Capability.Operation != api.OperationGenerate {
		t.Fatalf("batch or duplicate source changed the model: models=%v rejected=%v", models, rejected)
	}

	conflicting := recordRef{key: "other/model", record: sourceRecord{BaseModel: "model", Mode: "chat", SupportedModalities: []string{"text", "image"}, SupportedOutputModalities: []string{"text"}}}
	models, rejected = compileModels([]recordRef{first, conflicting})
	if len(models) != 0 || len(rejected) == 0 || !strings.Contains(rejected[0].Reason, "supported_modalities") {
		t.Fatalf("conflicting facts were not removed as a whole: models=%v rejected=%v", models, rejected)
	}
}

func TestDifferentBaseModelsRemainIndependent(t *testing.T) {
	models, rejected := compileModels([]recordRef{
		{key: "opencode-zen/gemini-3-flash", record: sourceRecord{BaseModel: "gemini-3-flash", Mode: "chat"}},
		{key: "databricks/gemini-3-flash", record: sourceRecord{
			BaseModel: "databricks-gemini-3-flash", Mode: "chat",
			SupportsVision: boolPtr(true), SupportsAudioInput: boolPtr(true), SupportsFunctionCalling: boolPtr(true),
		}},
	})
	if len(rejected) != 0 || len(models) != 2 {
		t.Fatalf("different BaseModels were merged or rejected: models=%v rejected=%v", models, rejected)
	}
	byID := make(map[string]modelcatalog.Config, len(models))
	for _, model := range models {
		byID[model.BaseModel] = model
	}
	if got := byID["gemini-3-flash"].Capability.InputModalities; len(got) != 1 || got[0] != api.ModalityText {
		t.Fatalf("service facts leaked into gemini-3-flash: %v", got)
	}
	if got := byID["databricks-gemini-3-flash"].Capability.InputModalities; len(got) != 3 {
		t.Fatalf("the second model lost its own facts: %v", got)
	}
}

func TestOneBaseModelCannotHaveTwoOperations(t *testing.T) {
	models, rejected := compileModels([]recordRef{
		{key: "model", record: sourceRecord{BaseModel: "model", Mode: "chat"}},
		{key: "provider/model", record: sourceRecord{BaseModel: "model", Mode: "audio_speech"}},
	})
	if len(models) != 0 || len(rejected) != 1 || !strings.Contains(rejected[0].Reason, "conflicting operations") {
		t.Fatalf("operation conflict was not fatal: models=%v rejected=%v", models, rejected)
	}
}

func TestMalformedBaseModelAndModeFailClosed(t *testing.T) {
	models, rejected := compileModels([]recordRef{
		{key: "provider/missing-mode", record: sourceRecord{BaseModel: "missing-mode"}},
		{key: "provider/unknown-mode", record: sourceRecord{BaseModel: "unknown-mode", Mode: "search"}},
	})
	if len(models) != 0 || len(rejected) != 2 {
		t.Fatalf("malformed source rows were published: models=%v rejected=%v", models, rejected)
	}
	seen := map[string]bool{}
	for _, diagnostic := range rejected {
		seen[diagnostic.Field] = true
	}
	if !seen["mode"] || !seen["operation"] {
		t.Fatalf("malformed mode diagnostics were incomplete: %v", rejected)
	}

	batch := recordRef{key: "provider/unknown:batch", record: sourceRecord{BaseModel: "unknown", Mode: "search"}}
	models, rejected = compileModels([]recordRef{batch})
	if len(models) != 0 || len(rejected) != 0 {
		t.Fatalf("delivery-only batch row entered model validation: models=%v rejected=%v", models, rejected)
	}
}

func TestExplicitSourceModalitiesAreExact(t *testing.T) {
	realtime := recordRef{key: "realtime", record: sourceRecord{
		BaseModel: "realtime", Mode: "realtime",
		SupportedModalities: []string{"audio"}, SupportedOutputModalities: []string{"text", "audio"},
		SupportsVision: boolPtr(true),
	}}
	compiled, present, err := compileForTest(realtime.key, []recordRef{realtime})
	if err != nil || !present {
		t.Fatalf("compile realtime: present=%v err=%v", present, err)
	}
	if got := compiled.Capability.InputModalities; len(got) != 1 || got[0] != api.ModalityAudio {
		t.Fatalf("explicit input modalities were widened: %v", got)
	}
	if got := compiled.Capability.OutputModalities; len(got) != 2 || got[0] != api.ModalityAudio || got[1] != api.ModalityText {
		t.Fatalf("explicit output modalities were lost: %v", got)
	}
}

func TestUnknownSourceModalityIsRejected(t *testing.T) {
	primary := recordRef{key: "code-model", record: sourceRecord{
		BaseModel: "code-model", Mode: "chat", SupportedModalities: []string{"text"}, SupportedOutputModalities: []string{"code"},
	}}
	_, _, err := compileForTest(primary.key, []recordRef{primary})
	var compileErr *compileError
	if !errors.As(err, &compileErr) || !strings.Contains(compileErr.Reason, "unsupported modality") {
		t.Fatalf("unknown source modality was not rejected: %T %v", err, err)
	}
}

func TestDisabledParametersAreNotPublished(t *testing.T) {
	primary := recordRef{key: "gpt-5.1-chat-latest", record: sourceRecord{
		BaseModel: "gpt-5.1-chat-latest", Mode: "chat", MaxOutputTokens: numberPtr("16384"),
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
	policy := compiled.Capability.Generation
	if policy == nil || policy.Temperature != nil || policy.TopP != nil || policy.MaxOutputTokens == nil {
		t.Fatalf("disabled parameters leaked into policy: %+v", policy)
	}
}

func TestStructuredOutputAndToolCallingEvidenceAreModelFacts(t *testing.T) {
	primary := recordRef{key: "structured-model", record: sourceRecord{
		BaseModel: "structured-model", Mode: "chat", SupportsFunctionCalling: boolPtr(true), SupportsResponseSchema: boolPtr(true),
	}}
	compiled, _, err := compileForTest(primary.key, []recordRef{primary})
	if err != nil {
		t.Fatal(err)
	}
	if len(compiled.Capability.Features) != 2 || compiled.Capability.Features[0] != api.FeatureStructured || compiled.Capability.Features[1] != api.FeatureToolCalls {
		t.Fatalf("model compatibility evidence was not retained deterministically: %v", compiled.Capability.Features)
	}
}

func TestInvalidSourceBoundsAreRejected(t *testing.T) {
	primary := recordRef{key: "bad-model", record: sourceRecord{
		BaseModel: "bad-model", Mode: "chat", ModelParameters: []sourceParameter{{ID: "temperature", Range: &sourceRange{Minimum: numberPtr("2"), Maximum: numberPtr("1")}}},
	}}
	_, _, err := compileForTest(primary.key, []recordRef{primary})
	var compileErr *compileError
	if !errors.As(err, &compileErr) || compileErr.Field != "operation" || !strings.Contains(compileErr.Reason, "temperature") {
		t.Fatalf("invalid source bounds were not rejected: %T %v", err, err)
	}
}

func TestOutputTokenUIDefaultDoesNotBecomeGatewayPolicy(t *testing.T) {
	primary := recordRef{key: "model", record: sourceRecord{
		BaseModel: "model", Mode: "chat", ModelParameters: []sourceParameter{{ID: "max_tokens", Default: json.RawMessage("32768"), Range: &sourceRange{Minimum: numberPtr("1"), Maximum: numberPtr("8192")}}},
	}}
	compiled, _, err := compileForTest(primary.key, []recordRef{primary})
	if err != nil {
		t.Fatalf("unrelated UI default rejected the model: %v", err)
	}
	if compiled.TokenLimits.MinOutputTokens != 1 || compiled.TokenLimits.MaxOutputTokens != 8192 {
		t.Fatalf("declared output bounds were not retained: %+v", compiled.TokenLimits)
	}
	encoded, encodeErr := json.Marshal(compiled.Capability.Generation.MaxOutputTokens)
	if encodeErr != nil || string(encoded) != `{}` {
		t.Fatalf("source UI default became Gateway policy: %s (%v)", encoded, encodeErr)
	}
}

func TestEmbeddingDimensionsAreModelOwned(t *testing.T) {
	primary := recordRef{key: "embedding-model", record: sourceRecord{
		BaseModel: "embedding-model", Mode: "embedding", OutputVectorSize: numberPtr("1536"),
		ModelParameters: []sourceParameter{{ID: "dimensions", Range: &sourceRange{Minimum: numberPtr("64"), Maximum: numberPtr("3072")}}},
	}}
	compiled, _, err := compileForTest(primary.key, []recordRef{primary})
	if err != nil {
		t.Fatal(err)
	}
	if compiled.Capability.Embedding == nil || compiled.Capability.Embedding.Dimensions == nil || compiled.Capability.Embedding.Dimensions.Default == nil || *compiled.Capability.Embedding.Dimensions.Default != 1536 {
		t.Fatalf("embedding dimension fact was lost: %+v", compiled.Capability.Embedding)
	}
	bad := recordRef{key: "bad-dimensions", record: sourceRecord{BaseModel: "bad-dimensions", Mode: "embedding", ModelParameters: []sourceParameter{{ID: "dimensions", Range: &sourceRange{Minimum: numberPtr("0")}}}}}
	_, _, err = compileForTest(bad.key, []recordRef{bad})
	var compileErr *compileError
	if !errors.As(err, &compileErr) || !strings.Contains(compileErr.Reason, "dimensions") {
		t.Fatalf("invalid embedding dimensions were accepted: %T %v", err, err)
	}
}

func TestCompilerUsesRuntimeCapabilityShapeOwner(t *testing.T) {
	cases := []recordRef{
		{key: "bad-generate", record: sourceRecord{BaseModel: "bad-generate", Mode: "chat", SupportedModalities: []string{"text"}, SupportedOutputModalities: []string{"embedding"}}},
		{key: "bad-realtime", record: sourceRecord{BaseModel: "bad-realtime", Mode: "realtime", SupportedModalities: []string{"image"}, SupportedOutputModalities: []string{"text"}}},
		{key: "bad-embedding", record: sourceRecord{BaseModel: "bad-embedding", Mode: "embedding", SupportedModalities: []string{"embedding"}, SupportedOutputModalities: []string{"embedding"}}},
	}
	for _, source := range cases {
		t.Run(source.key, func(t *testing.T) {
			_, present, err := compileForTest(source.key, []recordRef{source})
			var compileErr *compileError
			if present || !errors.As(err, &compileErr) || compileErr.Field != "capability" {
				t.Fatalf("shape owner did not reject compiler output: present=%v %T %v", present, err, err)
			}
		})
	}
}

func TestBooleanConflictsDeleteTheWholeModel(t *testing.T) {
	models, rejected := compileModels([]recordRef{
		{key: "model", record: sourceRecord{BaseModel: "model", Mode: "chat", SupportsFunctionCalling: boolPtr(true)}},
		{key: "provider/model", record: sourceRecord{BaseModel: "model", Mode: "chat", SupportsFunctionCalling: boolPtr(false)}},
	})
	if len(models) != 0 || len(rejected) == 0 {
		t.Fatalf("conflicting model boolean was published: models=%v rejected=%v", models, rejected)
	}
}

func TestDecodeSourceRecordsIsStrict(t *testing.T) {
	valid := `{"model":{"base_model":"model","mode":"chat"}}`
	if records, err := decodeSourceRecords([]byte(valid)); err != nil || len(records) != 1 {
		t.Fatalf("valid source snapshot failed: %v (%v)", records, err)
	}
	for _, input := range []string{"", "null", "{}", `{"model":null}`, `{"model":{}} {}`, `{"model":{}} garbage`, `{"model":{},"model":{}}`, `[]`} {
		if _, err := decodeSourceRecords([]byte(input)); err == nil {
			t.Errorf("decodeSourceRecords(%q) accepted malformed input", input)
		}
	}
}

func TestDeliveryMetadataDoesNotEnterModelArtifact(t *testing.T) {
	records, err := decodeSourceRecords([]byte(`{
		"gemini-2.5-pro": {"base_model":"gemini-2.5-pro","mode":"chat","supported_endpoints":["/v1/chat/completions","/v1/batch"],"supports_native_streaming":true},
		"openrouter/google/gemini-2.5-pro:batch": {"base_model":"gemini-2.5-pro","mode":"chat","supported_endpoints":["/v1/batch"]}
	}`))
	if err != nil {
		t.Fatal(err)
	}
	models, rejected := compileModels(records)
	if len(rejected) != 0 || len(models) != 1 {
		t.Fatalf("compiled models = %d, rejected = %v", len(models), rejected)
	}
	encoded, err := json.Marshal(models[0])
	if err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range []string{"modes", "endpoint", "stream", "usage", "provider"} {
		if strings.Contains(string(encoded), forbidden) {
			t.Errorf("service/protocol fact %q entered model artifact: %s", forbidden, encoded)
		}
	}
}

func TestSyntheticNamesAreNotModels(t *testing.T) {
	for _, modelID := range []string{"conservative", "creative", "preset/deep-research", "claude-opus-4-8-high", "deepseek-v4-flash-max", "o3-mini-high", "grok-3-search", "gpt-5:batch"} {
		if !syntheticModelID(modelID) {
			t.Errorf("syntheticModelID(%q) = false", modelID)
		}
	}
	models, rejected := compileModels([]recordRef{{key: "preset", record: sourceRecord{BaseModel: "conservative", Mode: "chat"}}})
	if len(models) != 0 || len(rejected) != 0 {
		t.Fatalf("synthetic BaseModel was published: models=%v rejected=%v", models, rejected)
	}
}

func TestCatalogEncodingRejectsQualifiedBaseModel(t *testing.T) {
	_, err := encodeCatalog(modelcatalog.CatalogDocument{
		SchemaVersion: modelcatalog.CatalogSchemaVersion,
		Models:        []modelcatalog.Config{{BaseModel: "provider/model"}},
	})
	if err == nil || !strings.Contains(err.Error(), "bare model name") {
		t.Fatalf("qualified BaseModel was accepted: %v", err)
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
	data, err := os.ReadFile(catalogPath)
	if err != nil || string(data) != "new catalog" {
		t.Fatalf("published artifact = %q, %v", data, err)
	}
	entries, err := os.ReadDir(directory)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 {
		t.Fatalf("publication left staging files: %v", entries)
	}
}

func numberPtr(value string) *json.Number {
	result := json.Number(value)
	return &result
}

func boolPtr(value bool) *bool { return &value }
