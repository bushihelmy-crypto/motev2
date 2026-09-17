package model

import (
	"errors"
	"math"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

func TestGenerationResolutionPreservesExactBoundsAndExplicitZero(t *testing.T) {
	config := testModelConfig("numeric-boundaries")
	config.TokenLimits = TokenLimits{MinOutputTokens: 1, MaxOutputTokens: math.MaxInt64}
	config.Capability.Generation = &GenerationPolicy{
		Temperature:     &NumericParameter[float64]{Minimum: pointer(0.0), Maximum: pointer(2.0), Default: pointer(1.0)},
		TopP:            &NumericParameter[float64]{Minimum: pointer(math.SmallestNonzeroFloat64), Maximum: pointer(1.0), Default: pointer(0.5)},
		MaxOutputTokens: &OutputTokenParameter{},
		Stop:            &StopParameter{Default: []string{"DEFAULT"}},
		Seed:            &NumericParameter[int64]{Minimum: pointer(int64(0)), Maximum: pointer(int64(math.MaxInt64)), Default: pointer(int64(7))},
	}
	catalog, err := newCatalog([]Config{config})
	if err != nil {
		t.Fatal(err)
	}
	definition, _ := catalog.Lookup(config.BaseModel)
	capability, _ := definition.Capability(api.OperationGenerate)

	lower := capability.ResolveGenerationParameters(api.GenerationParameters{
		Temperature:     pointer(0.0),
		TopP:            pointer(math.SmallestNonzeroFloat64),
		MaxOutputTokens: pointer(int64(1)),
		Stop:            []string{},
		Seed:            pointer(int64(0)),
	})
	if lower.Temperature == nil || *lower.Temperature != 0 ||
		lower.TopP == nil || *lower.TopP != math.SmallestNonzeroFloat64 ||
		lower.MaxOutputTokens == nil || *lower.MaxOutputTokens != 1 ||
		lower.Seed == nil || *lower.Seed != 0 || lower.Stop == nil || len(lower.Stop) != 0 {
		t.Fatalf("explicit lower bounds or empty stop became defaults: %+v", lower)
	}

	upper := capability.ResolveGenerationParameters(api.GenerationParameters{
		Temperature:     pointer(2.0),
		TopP:            pointer(1.0),
		MaxOutputTokens: pointer(int64(math.MaxInt64)),
		Seed:            pointer(int64(math.MaxInt64)),
	})
	if upper.Temperature == nil || *upper.Temperature != 2 ||
		upper.TopP == nil || *upper.TopP != 1 ||
		upper.MaxOutputTokens == nil || *upper.MaxOutputTokens != math.MaxInt64 ||
		upper.Seed == nil || *upper.Seed != math.MaxInt64 {
		t.Fatalf("exact upper bounds changed: %+v", upper)
	}
}

func TestOutputTokenDefaultUsesOnlyKnownModelBounds(t *testing.T) {
	for _, testCase := range []struct {
		name    string
		limits  TokenLimits
		want    int64
		request *int64
	}{
		{name: "gateway default with unknown bounds", want: gatewayDefaultMaxOutputTokens},
		{name: "known minimum raises default", limits: TokenLimits{MinOutputTokens: 8192}, want: 8192},
		{name: "known maximum lowers default", limits: TokenLimits{MaxOutputTokens: 1024}, want: 1024},
		{name: "explicit below minimum", limits: TokenLimits{MinOutputTokens: 8}, request: pointer(int64(1)), want: 8},
		{name: "explicit above maximum", limits: TokenLimits{MaxOutputTokens: 8}, request: pointer(int64(9)), want: 8},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			got := resolveOutputTokens(&OutputTokenParameter{}, testCase.request, testCase.limits.MinOutputTokens, testCase.limits.MaxOutputTokens)
			if got == nil || *got != testCase.want {
				t.Fatalf("resolved output tokens = %v, want %d", got, testCase.want)
			}
		})
	}
	if got := resolveOutputTokens(nil, pointer(int64(1)), 1, 2); got != nil {
		t.Fatalf("unsupported output token parameter was retained: %v", *got)
	}
}

func TestReasoningResolutionPreservesEveryDeclaredEffortExactly(t *testing.T) {
	efforts := []api.ReasoningEffort{
		api.ReasoningEffortMinimal,
		api.ReasoningEffortLow,
		api.ReasoningEffortMedium,
		api.ReasoningEffortHigh,
		api.ReasoningEffortXHigh,
		api.ReasoningEffortMax,
	}
	config := testModelConfig("reasoning-matrix")
	config.Capability.Reasoning = &ReasoningPolicy{ThinkingModes: []ThinkingModePolicy{
		{Thinking: api.ThinkingDisabled},
		{Thinking: api.ThinkingEnabled, Efforts: efforts},
		{Thinking: api.ThinkingAdaptive, Efforts: efforts},
	}}
	catalog, err := newCatalog([]Config{config})
	if err != nil {
		t.Fatal(err)
	}
	definition, _ := catalog.Lookup(config.BaseModel)
	capability, _ := definition.Capability(api.OperationGenerate)

	for _, mode := range []api.ThinkingMode{api.ThinkingEnabled, api.ThinkingAdaptive} {
		for _, effort := range efforts {
			resolved, resolveErr := capability.ResolveReasoning(&api.ReasoningConfig{Thinking: mode, Effort: effort})
			if resolveErr != nil || resolved == nil || resolved.Thinking != mode || resolved.Effort != effort {
				t.Errorf("%s + %s resolved to %+v, %v", mode, effort, resolved, resolveErr)
			}
		}
	}
	disabled, err := capability.ResolveReasoning(&api.ReasoningConfig{Thinking: api.ThinkingDisabled})
	if err != nil || disabled == nil || disabled.Thinking != api.ThinkingDisabled || disabled.Effort != "" {
		t.Fatalf("disabled reasoning changed: %+v %v", disabled, err)
	}

	max, err := capability.ResolveReasoning(&api.ReasoningConfig{Thinking: api.ThinkingEnabled, Effort: api.ReasoningEffortMax})
	if err != nil || max.Effort != api.ReasoningEffortMax {
		t.Fatalf("max was not preserved exactly: %+v %v", max, err)
	}
	xhigh, err := capability.ResolveReasoning(&api.ReasoningConfig{Thinking: api.ThinkingEnabled, Effort: api.ReasoningEffortXHigh})
	if err != nil || xhigh.Effort != api.ReasoningEffortXHigh || xhigh.Effort == max.Effort {
		t.Fatalf("xhigh was treated as max: max=%+v xhigh=%+v err=%v", max, xhigh, err)
	}
}

func TestReasoningDefaultsRemainModeLocal(t *testing.T) {
	enabledDefault := api.ReasoningEffortLow
	adaptiveDefault := api.ReasoningEffortHigh
	defaultThinking := api.ThinkingAdaptive
	config := testModelConfig("reasoning-defaults")
	config.Capability.Reasoning = &ReasoningPolicy{
		DefaultThinking: &defaultThinking,
		ThinkingModes: []ThinkingModePolicy{
			{Thinking: api.ThinkingEnabled, Efforts: []api.ReasoningEffort{enabledDefault}, DefaultEffort: &enabledDefault},
			{Thinking: api.ThinkingAdaptive, Efforts: []api.ReasoningEffort{adaptiveDefault}, DefaultEffort: &adaptiveDefault},
		},
	}
	catalog, err := newCatalog([]Config{config})
	if err != nil {
		t.Fatal(err)
	}
	definition, _ := catalog.Lookup(config.BaseModel)
	capability, _ := definition.Capability(api.OperationGenerate)

	resolved, err := capability.ResolveReasoning(nil)
	if err != nil || resolved == nil || resolved.Thinking != api.ThinkingAdaptive || resolved.Effort != adaptiveDefault {
		t.Fatalf("default mode did not use its own effort: %+v %v", resolved, err)
	}
	resolved, err = capability.ResolveReasoning(&api.ReasoningConfig{Thinking: api.ThinkingEnabled})
	if err != nil || resolved == nil || resolved.Thinking != api.ThinkingEnabled || resolved.Effort != enabledDefault {
		t.Fatalf("explicit mode did not use its own effort: %+v %v", resolved, err)
	}
}

func TestTokenLimitZeroMeansUnknownAndExactLimitsAreValid(t *testing.T) {
	valid := []TokenLimits{
		{},
		{MaxInputTokens: math.MaxInt64, MinOutputTokens: 1, MaxOutputTokens: math.MaxInt64},
		{ContextWindowTokens: 1, MaxInputTokens: 1, MinOutputTokens: 1, MaxOutputTokens: 1},
		{MinOutputTokens: 7},
	}
	for _, limits := range valid {
		if reason := limits.validationError(); reason != "" {
			t.Errorf("valid limits %+v rejected: %s", limits, reason)
		}
	}

	invalid := []TokenLimits{
		{ContextWindowTokens: -1},
		{MaxInputTokens: -1},
		{MinOutputTokens: -1},
		{MaxOutputTokens: -1},
		{MinOutputTokens: 2, MaxOutputTokens: 1},
		{ContextWindowTokens: 1, MaxInputTokens: 2},
		{ContextWindowTokens: 1, MinOutputTokens: 2},
		{ContextWindowTokens: 1, MaxOutputTokens: 2},
	}
	for _, limits := range invalid {
		if reason := limits.validationError(); reason == "" {
			t.Errorf("invalid limits %+v accepted", limits)
		}
	}
}

func TestCatalogLookupAndOperationResolutionStayExact(t *testing.T) {
	catalog, err := newCatalog([]Config{testModelConfig("Exact.Model:1")})
	if err != nil {
		t.Fatal(err)
	}
	definition, err := catalog.Lookup("Exact.Model:1")
	if err != nil {
		t.Fatal(err)
	}
	for _, candidate := range []string{"exact.model:1", "Exact.Model", "provider/Exact.Model:1", "Exact.Model:1-latest"} {
		_, lookupErr := catalog.Lookup(candidate)
		var notFound *ModelNotFoundError
		if !errors.As(lookupErr, &notFound) || notFound.BaseModel != candidate {
			t.Errorf("non-exact candidate %q did not return its own not-found error: %T %v", candidate, lookupErr, lookupErr)
		}
	}
	empty := api.Operation("")
	_, err = definition.ResolveOperation(&empty)
	var mismatch *OperationMismatchError
	if !errors.As(err, &mismatch) || mismatch.Requested != "" || mismatch.Available != api.OperationGenerate {
		t.Fatalf("explicit empty operation became omission: %T %v", err, err)
	}
}
