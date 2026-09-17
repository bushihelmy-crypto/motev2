package api

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestFeatureValidityHasOneCanonicalVocabulary(t *testing.T) {
	for _, feature := range []Feature{
		FeatureToolCalls,
		FeatureStructured,
		FeaturePromptCache,
		FeatureUsage,
	} {
		if !feature.IsValid() {
			t.Fatalf("known feature %q was rejected", feature)
		}
	}
	if Feature("unknown").IsValid() {
		t.Fatal("unknown feature was accepted")
	}
}

func TestOperationVocabularyAndPrimaryModalities(t *testing.T) {
	operations := []struct {
		operation Operation
		modality  Modality
		llm       bool
		media     bool
		primary   bool
	}{
		{OperationGenerate, ModalityText, true, false, true},
		{OperationEmbedding, "", false, false, false},
		{OperationRerank, "", false, false, false},
		{OperationImageGeneration, ModalityImage, false, true, true},
		{OperationAudioGeneration, ModalityAudio, false, true, true},
		{OperationAudioTranscription, ModalityAudio, false, true, true},
		{OperationMusicGeneration, ModalityMusic, false, true, true},
		{OperationVideoGeneration, ModalityVideo, false, true, true},
		{OperationRealtime, ModalityAudio, true, false, true},
	}
	for _, testCase := range operations {
		if !testCase.operation.IsValid() {
			t.Fatalf("known operation %q was rejected", testCase.operation)
		}
		modality, ok := testCase.operation.PrimaryModality()
		if modality != testCase.modality || ok != testCase.primary {
			t.Fatalf("operation %q primary modality = %q, %v; want %q, %v", testCase.operation, modality, ok, testCase.modality, testCase.primary)
		}
		if testCase.operation.IsLLM() != testCase.llm || testCase.operation.IsMedia() != testCase.media {
			t.Fatalf("operation %q profile classification changed", testCase.operation)
		}
	}
	unknown := Operation("unknown")
	if unknown.IsValid() || unknown.IsLLM() || unknown.IsMedia() {
		t.Fatal("unknown operation entered a supported profile")
	}
	if modality, ok := unknown.PrimaryModality(); ok || modality != "" {
		t.Fatalf("unknown operation has a primary modality: %q %v", modality, ok)
	}
}

func TestModalityAndDeliveryModeVocabularies(t *testing.T) {
	for _, modality := range []Modality{ModalityText, ModalityImage, ModalityAudio, ModalityMusic, ModalityVideo, ModalityEmbedding} {
		if !modality.IsValid() {
			t.Fatalf("known modality %q was rejected", modality)
		}
	}
	if Modality("unknown").IsValid() {
		t.Fatal("unknown modality was accepted")
	}
	for _, mode := range []DeliveryMode{ModeUnary, ModeServerStream, ModeDuplex, ModeAsync} {
		if !mode.IsValid() {
			t.Fatalf("known delivery mode %q was rejected", mode)
		}
	}
	if DeliveryMode("unknown").IsValid() {
		t.Fatal("unknown delivery mode was accepted")
	}
}

func TestRequestOperationMayBeOmittedOnWire(t *testing.T) {
	request := LLMRequest{
		Kind:          RequestKindLLM,
		SchemaVersion: 1,
		OperationID:   "op-1",
		BaseModel:     "model.text",
		Modality:      ModalityText,
		Mode:          ModeUnary,
		Features:      []Feature{},
	}
	encoded, err := json.Marshal(request)
	if err != nil {
		t.Fatalf("marshal request without operation: %v", err)
	}
	if strings.Contains(string(encoded), `"operation"`) {
		t.Fatalf("omitted operation leaked onto the wire: %s", encoded)
	}

	var decoded LLMRequest
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatalf("unmarshal request without operation: %v", err)
	}
	if decoded.Operation != nil {
		t.Fatalf("missing operation did not retain omission: %v", decoded.Operation)
	}
}
