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
