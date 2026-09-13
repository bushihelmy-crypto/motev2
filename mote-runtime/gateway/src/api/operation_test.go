package api

import "testing"

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
