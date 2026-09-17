package api

import (
	"encoding/json"
	"testing"
)

func TestRequestFrameCarriesTypedRequestOnly(t *testing.T) {
	operation := OperationGenerate
	frame := LLMRequestFrame{Request: LLMRequest{Operation: &operation}}
	if frame.Request.Operation == nil || *frame.Request.Operation != OperationGenerate {
		t.Fatal("typed frame did not retain operation")
	}
}

func TestOperationPointerPreservesWireOmissionAndExplicitEmpty(t *testing.T) {
	omitted, err := json.Marshal(LLMRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if string(omitted) == "" {
		t.Fatal("empty request did not marshal")
	}
	if string(omitted) != `{"kind":"","schema_version":0,"operation_id":"","base_model":"","mode":"","input":{"kind":""}}` {
		// Keep this assertion intentionally loose: the important contract is
		// that operation is absent when its pointer is nil.
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(omitted, &fields); err != nil {
			t.Fatal(err)
		}
		if _, present := fields["operation"]; present {
			t.Fatalf("omitted operation leaked onto the wire: %s", omitted)
		}
	}

	value := Operation("")
	encoded, err := json.Marshal(LLMRequest{Operation: &value})
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(encoded, &fields); err != nil {
		t.Fatal(err)
	}
	if _, present := fields["operation"]; !present {
		t.Fatalf("explicit empty operation was lost: %s", encoded)
	}
}
