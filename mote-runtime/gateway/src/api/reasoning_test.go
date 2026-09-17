package api

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestReasoningVocabularyAndCombinations(t *testing.T) {
	for _, mode := range []ThinkingMode{ThinkingDisabled, ThinkingEnabled, ThinkingAdaptive} {
		if !mode.IsValid() {
			t.Fatalf("known thinking mode %q was rejected", mode)
		}
	}
	for _, effort := range []ReasoningEffort{
		ReasoningEffortMinimal,
		ReasoningEffortLow,
		ReasoningEffortMedium,
		ReasoningEffortHigh,
		ReasoningEffortXHigh,
		ReasoningEffortMax,
	} {
		if !effort.IsValid() {
			t.Fatalf("known reasoning effort %q was rejected", effort)
		}
	}
	if ThinkingMode("unknown").IsValid() || ReasoningEffort("unknown").IsValid() {
		t.Fatal("unknown reasoning vocabulary was accepted")
	}
	if ReasoningEffort("none").IsValid() {
		t.Fatal("none must be represented by disabled thinking")
	}

	valid := []ReasoningConfig{
		{Thinking: ThinkingDisabled},
		{Thinking: ThinkingEnabled},
		{Thinking: ThinkingAdaptive, Effort: ReasoningEffortHigh},
		{Effort: ReasoningEffortLow},
	}
	for _, config := range valid {
		if err := config.Validate(); err != nil {
			t.Errorf("valid reasoning config %+v rejected: %v", config, err)
		}
	}
	for _, config := range []ReasoningConfig{
		{Thinking: ThinkingDisabled, Effort: ReasoningEffortHigh},
		{Thinking: ThinkingMode("other")},
		{Effort: ReasoningEffort("none")},
		{},
	} {
		if err := config.Validate(); err == nil {
			t.Errorf("invalid reasoning config %+v was accepted", config)
		}
	}
}

func TestReasoningConfigValidatesTheCompleteModeEffortMatrix(t *testing.T) {
	efforts := []ReasoningEffort{
		ReasoningEffortMinimal,
		ReasoningEffortLow,
		ReasoningEffortMedium,
		ReasoningEffortHigh,
		ReasoningEffortXHigh,
		ReasoningEffortMax,
	}
	for _, mode := range []ThinkingMode{ThinkingDisabled, ThinkingEnabled, ThinkingAdaptive} {
		if err := (ReasoningConfig{Thinking: mode}).Validate(); err != nil {
			t.Errorf("mode-only %q rejected: %v", mode, err)
		}
		for _, effort := range efforts {
			err := (ReasoningConfig{Thinking: mode, Effort: effort}).Validate()
			if mode == ThinkingDisabled && err == nil {
				t.Errorf("disabled + %q was accepted", effort)
			}
			if mode != ThinkingDisabled && err != nil {
				t.Errorf("%q + %q rejected: %v", mode, effort, err)
			}
		}
	}
	for _, effort := range efforts {
		if err := (ReasoningConfig{Effort: effort}).Validate(); err != nil {
			t.Errorf("effort-only %q rejected: %v", effort, err)
		}
	}
}

func TestReasoningConfigRoundTripsOnLLMInputOnly(t *testing.T) {
	input := LLMInput{
		Kind: "generate",
		Reasoning: &ReasoningConfig{
			Thinking: ThinkingAdaptive,
			Effort:   ReasoningEffortHigh,
		},
	}
	encoded, err := json.Marshal(input)
	if err != nil {
		t.Fatalf("marshal LLM input: %v", err)
	}
	var decoded LLMInput
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatalf("unmarshal LLM input: %v", err)
	}
	if decoded.Reasoning == nil || decoded.Reasoning.Thinking != ThinkingAdaptive || decoded.Reasoning.Effort != ReasoningEffortHigh {
		t.Fatalf("reasoning config did not round-trip: %+v", decoded.Reasoning)
	}

	without := LLMInput{Kind: "generate"}
	encoded, err = json.Marshal(without)
	if err != nil {
		t.Fatalf("marshal LLM input without reasoning: %v", err)
	}
	if strings.Contains(string(encoded), `"reasoning"`) {
		t.Fatalf("omitted reasoning leaked onto wire: %s", encoded)
	}
}
