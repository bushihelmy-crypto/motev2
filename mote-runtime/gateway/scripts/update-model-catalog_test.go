package main

import (
	"encoding/json"
	"go/ast"
	"testing"
)

func TestOperationInferenceKeepsModelAndRequestModeSeparate(t *testing.T) {
	cases := []struct {
		name     string
		modelID  string
		mode     string
		outputs  []string
		expected string
	}{
		{name: "general model on image price row", modelID: "anthropic/claude-3-5-sonnet-20241022", mode: "image_generation", expected: "generate"},
		{name: "general model with mixed image output", modelID: "deep-research-pro", mode: "image_generation", outputs: []string{"text", "image"}, expected: "generate"},
		{name: "speech model sharing an image family name", modelID: "@cf/deepgram/flux", mode: "audio_transcription", expected: "audio_transcription"},
		{name: "actual image model", modelID: "gpt-image-1", mode: "image_generation", expected: "image_generation"},
		{name: "actual image model with output evidence", modelID: "custom-vision-model", mode: "image_generation", outputs: []string{"image"}, expected: "image_generation"},
		{name: "reranker mislabeled as chat", modelID: "@cf/baai/bge-reranker-base", mode: "chat", expected: "rerank"},
		{name: "embedding mislabeled as chat", modelID: "titan-embed-text-v2", mode: "chat", expected: "embedding"},
		{name: "video model", modelID: "wan-2.6-t2v", mode: "video_generation", expected: "video_generation"},
		{name: "realtime transcription", modelID: "voxtral-mini-realtime", mode: "realtime", expected: "audio_transcription"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			ref := recordRef{record: sourceRecord{Mode: testCase.mode, SupportedOutputModalities: testCase.outputs}}
			if got := operationForRecord(testCase.modelID, ref); got != testCase.expected {
				t.Fatalf("operationForRecord(%q, %q) = %q, want %q", testCase.modelID, testCase.mode, got, testCase.expected)
			}
		})
	}
}

func TestSyntheticRequestPresetsAreNotModels(t *testing.T) {
	for _, modelID := range []string{
		"conservative", "creative", "edit", "erase", "fast", "inpaint", "outpaint",
		"preset/deep-research", "claude-opus-4-8-high", "deepseek-v4-flash-max",
		"o3-mini-high", "grok-3-search",
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

func TestResolveStringLiteralSupportsModelConstants(t *testing.T) {
	constants := map[string]string{"ModelName": "black-forest-labs/flux-1.1-pro"}
	if value, ok := resolveStringLiteral(&ast.Ident{Name: "ModelName"}, constants); !ok || value != constants["ModelName"] {
		t.Fatalf("resolved identifier = %q, %v", value, ok)
	}
	if value, ok := resolveStringLiteral(&ast.Ident{Name: "Missing"}, constants); ok || value != "" {
		t.Fatalf("missing identifier resolved as %q, %v", value, ok)
	}
}

func TestCompileModesRetainsUnaryAndAddsDeclaredAsyncOrStreamingModes(t *testing.T) {
	record := recordRef{key: "openrouter/openai/text-embedding-3-large:batch", record: sourceRecord{
		SupportedEndpoints: []string{"embeddings", "batch"},
	}}
	modes := compileModes("embedding", []recordRef{record})
	if len(modes) != 1 || modes[0] != "async" {
		t.Fatalf("compileModes(embedding, batch) = %v, want [async]", modes)
	}
	streaming := recordRef{record: sourceRecord{SupportsNativeStreaming: true}}
	modes = compileModes("generate", []recordRef{streaming})
	if len(modes) != 2 || modes[0] != "server_stream" || modes[1] != "unary" {
		t.Fatalf("compileModes(generate, streaming) = %v, want [server_stream unary]", modes)
	}
}

func TestCompileGenerationPolicyUsesUnifiedOutputDefault(t *testing.T) {
	maxTokens := json.Number("2048")
	record := recordRef{key: "small-model", record: sourceRecord{
		MaxOutputTokens: &maxTokens,
		ModelParameters: []sourceParameter{{
			ID:      "max_tokens",
			Default: json.RawMessage("128"),
		}},
	}}
	policy := compileGenerationPolicy([]recordRef{record}, record)
	if policy == nil || policy.MaxOutputTokens == nil || policy.MaxOutputTokens.Default == nil {
		t.Fatal("max_output_tokens policy was not generated")
	}
	if *policy.MaxOutputTokens.Default != defaultMaxOutputTokens {
		t.Fatalf("max_output_tokens default = %d, want %d", *policy.MaxOutputTokens.Default, defaultMaxOutputTokens)
	}
}

func TestCompileModelClampsUnifiedOutputDefaultToKnownLimit(t *testing.T) {
	minimum := json.Number("1")
	maximum := json.Number("2048")
	record := recordRef{key: "small-model", record: sourceRecord{
		BaseModel: "small-model",
		Mode:      "chat",
		ModelParameters: []sourceParameter{{
			ID:    "max_tokens",
			Range: &sourceRange{Minimum: &minimum, Maximum: &maximum},
		}},
	}}
	compiled, ok := compileModel("small-model", []recordRef{record}, nil, nil)
	if !ok || compiled.Operations[0].Generation == nil || compiled.Operations[0].Generation.MaxOutputTokens == nil ||
		compiled.Operations[0].Generation.MaxOutputTokens.Default == nil {
		t.Fatal("small model did not receive an output-token default")
	}
	if got := *compiled.Operations[0].Generation.MaxOutputTokens.Default; got != 2048 {
		t.Fatalf("clamped max_output_tokens default = %d, want 2048", got)
	}
}
