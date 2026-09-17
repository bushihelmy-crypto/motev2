package model

import (
	"fmt"
	"slices"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

type number interface {
	~float64 | ~int64
}

// gatewayDefaultMaxOutputTokens is Gateway parameter-resolution policy, not a
// model fact. Model-owned minimum and maximum values remain in TokenLimits.
const gatewayDefaultMaxOutputTokens int64 = 4096

// NumericParameter declares support for one numeric control. Bounds and a
// default are independently optional: nil bounds mean known support with an
// unknown boundary, so an explicit request is retained without invented
// clamping.
type NumericParameter[T number] struct {
	Minimum *T `json:"minimum,omitempty"`
	Maximum *T `json:"maximum,omitempty"`
	Default *T `json:"default,omitempty"`
}

// OutputTokenParameter is a presence marker declaring model support for
// max_output_tokens. The effective default is Gateway policy; independently
// known model bounds have one owner: Config.TokenLimits.
type OutputTokenParameter struct {
}

// StopParameter declares support for stop sequences and an optional default.
type StopParameter struct {
	Default []string `json:"default,omitempty"`
}

// GenerationPolicy is the single model-owned rule for optional controls in
// api.GenerationParameters. A nil field means an explicit request value is
// omitted before protocol encoding.
type GenerationPolicy struct {
	Temperature     *NumericParameter[float64] `json:"temperature,omitempty"`
	TopP            *NumericParameter[float64] `json:"top_p,omitempty"`
	MaxOutputTokens *OutputTokenParameter      `json:"max_output_tokens,omitempty"`
	Stop            *StopParameter             `json:"stop,omitempty"`
	Seed            *NumericParameter[int64]   `json:"seed,omitempty"`
}

// ThinkingModePolicy describes the effort values a model accepts for one
// thinking mode. Keeping the values beside the mode prevents a caller from
// combining two individually supported values that the model does not support
// together. An empty Efforts slice means that the mode is known but its effort
// controls are not exposed by the model.
type ThinkingModePolicy struct {
	Thinking      api.ThinkingMode      `json:"thinking"`
	Efforts       []api.ReasoningEffort `json:"efforts,omitempty"`
	DefaultEffort *api.ReasoningEffort  `json:"default_effort,omitempty"`
}

// ReasoningPolicy is the model-owned reasoning capability. It contains no
// provider or protocol field: adapters decide how a supported abstract mode
// is encoded for a selected upstream. DefaultThinking is optional because a
// model specification may leave its default to the upstream API.
type ReasoningPolicy struct {
	ThinkingModes   []ThinkingModePolicy `json:"thinking_modes"`
	DefaultThinking *api.ThinkingMode    `json:"default_thinking,omitempty"`
}

// ReasoningRequestError reports a malformed or internally ambiguous request
// preference at the model boundary.
type ReasoningRequestError struct {
	BaseModel string
	Reason    string
}

func (err *ReasoningRequestError) Error() string {
	return fmt.Sprintf("invalid reasoning request for model %q: %s", err.BaseModel, err.Reason)
}

// ReasoningUnsupportedError reports a valid reasoning preference that the
// selected model has not declared support for. It deliberately identifies the
// exact selected model; no alternate model is suggested or selected.
type ReasoningUnsupportedError struct {
	BaseModel string
	Thinking  api.ThinkingMode
	Effort    api.ReasoningEffort
	Reason    string
}

func (err *ReasoningUnsupportedError) Error() string {
	preference := ""
	if err.Thinking != "" {
		preference = " thinking=" + fmt.Sprintf("%q", err.Thinking)
	}
	if err.Effort != "" {
		preference += " effort=" + fmt.Sprintf("%q", err.Effort)
	}
	return fmt.Sprintf("model %q does not support reasoning%s: %s", err.BaseModel, preference, err.Reason)
}

// EmbeddingPolicy owns only embedding-specific controls. FixedDimensions is a
// fixed output width and filters a requested dimensions value. Dimensions is
// non-nil only when the model lets the caller choose the width. The two forms
// are mutually exclusive so the default has one owner.
type EmbeddingPolicy struct {
	FixedDimensions *int64                   `json:"fixed_dimensions,omitempty"`
	Dimensions      *NumericParameter[int64] `json:"dimensions,omitempty"`
}

// CapabilityConfig describes the one semantic operation identified by a
// BaseModel. Delivery modes belong to protocol and service capabilities; this
// model dimension contains model-native facts and the explicitly retained
// structured-output compatibility evidence.
type CapabilityConfig struct {
	Operation        api.Operation     `json:"operation"`
	InputModalities  []api.Modality    `json:"input_modalities"`
	OutputModalities []api.Modality    `json:"output_modalities"`
	Features         []api.Feature     `json:"features,omitempty"`
	Generation       *GenerationPolicy `json:"generation,omitempty"`
	Embedding        *EmbeddingPolicy  `json:"embedding,omitempty"`
	Reasoning        *ReasoningPolicy  `json:"reasoning,omitempty"`
}

// Capability is an immutable view of one normalized CapabilityConfig.
type Capability struct {
	definition Definition
}

// BaseModel returns the exact model identity owning this capability.
func (capability Capability) BaseModel() string { return capability.definition.BaseModel() }

// SupportsInputModality reports whether this operation accepts the modality.
func (capability Capability) SupportsInputModality(modality api.Modality) bool {
	operation := capability.definition.config.Capability
	return slices.Contains(operation.InputModalities, modality)
}

// SupportsOutputModality reports whether this operation produces the modality.
func (capability Capability) SupportsOutputModality(modality api.Modality) bool {
	operation := capability.definition.config.Capability
	return slices.Contains(operation.OutputModalities, modality)
}

// SupportsFeature reports whether this operation publishes the model-side
// compatibility evidence for a required feature. For structured output,
// admission must additionally verify protocol and service support.
func (capability Capability) SupportsFeature(feature api.Feature) bool {
	operation := capability.definition.config.Capability
	return slices.Contains(operation.Features, feature)
}

// ResolveGenerationParameters applies model defaults and then explicit request
// values. Known unsupported values are omitted. Supported numeric values are
// clamped only at boundaries known by the selected model.
func (capability Capability) ResolveGenerationParameters(
	requested api.GenerationParameters,
) api.GenerationParameters {
	definition := capability.definition.config
	return resolveGenerationPolicy(
		definition.Capability.Generation,
		requested,
		definition.TokenLimits,
	)
}

// ResolveReasoning applies the selected model's optional defaults and checks a
// caller's reasoning preference. It returns nil when neither the caller nor
// the catalog supplies a value. Protocol and service expressibility checks
// happen in admission after this model-only step.
func (capability Capability) ResolveReasoning(
	requested *api.ReasoningConfig,
) (*api.ReasoningConfig, error) {
	policy := capability.definition.config.Capability.Reasoning
	return resolveReasoningPolicy(capability.definition.BaseModel(), policy, requested)
}

// ResolveEmbeddingDimensions applies an adjustable embedding dimension default
// and explicit request, clamping it to known bounds. It returns nil for a fixed
// width or a model that does not support the dimensions request parameter.
func (capability Capability) ResolveEmbeddingDimensions(requested *int64) *int64 {
	operation := capability.definition.config.Capability
	if operation.Embedding == nil {
		return nil
	}
	return resolveNumeric(operation.Embedding.Dimensions, requested)
}

// DefaultEmbeddingDimensions returns a known fixed or adjustable default
// output width. False means the catalog has no authoritative width.
func (capability Capability) DefaultEmbeddingDimensions() (int64, bool) {
	operation := capability.definition.config.Capability
	if operation.Embedding == nil {
		return 0, false
	}
	if operation.Embedding.FixedDimensions != nil {
		return *operation.Embedding.FixedDimensions, true
	}
	if operation.Embedding.Dimensions != nil && operation.Embedding.Dimensions.Default != nil {
		return *operation.Embedding.Dimensions.Default, true
	}
	return 0, false
}

func resolveGenerationPolicy(
	policy *GenerationPolicy,
	requested api.GenerationParameters,
	limits TokenLimits,
) api.GenerationParameters {
	if policy == nil {
		return api.GenerationParameters{}
	}
	return api.GenerationParameters{
		Temperature: resolveNumeric(policy.Temperature, requested.Temperature),
		TopP:        resolveNumeric(policy.TopP, requested.TopP),
		MaxOutputTokens: resolveOutputTokens(
			policy.MaxOutputTokens,
			requested.MaxOutputTokens,
			limits.MinOutputTokens,
			limits.MaxOutputTokens,
		),
		Stop: resolveStop(policy.Stop, requested.Stop),
		Seed: resolveNumeric(policy.Seed, requested.Seed),
	}
}

func resolveReasoningPolicy(
	baseModel string,
	policy *ReasoningPolicy,
	requested *api.ReasoningConfig,
) (*api.ReasoningConfig, error) {
	if err := validateReasoningRequest(baseModel, requested); err != nil {
		return nil, err
	}
	if policy == nil {
		return resolveUndeclaredReasoning(baseModel, requested)
	}

	thinking, modePolicy, err := selectReasoningMode(baseModel, policy, requested)
	if err != nil {
		return nil, err
	}
	effort := resolveRequestedEffort(modePolicy, requested)
	if err := validateResolvedReasoning(baseModel, modePolicy, thinking, effort); err != nil {
		return nil, err
	}
	if thinking == "" && effort == "" {
		return nil, nil
	}
	return &api.ReasoningConfig{Thinking: thinking, Effort: effort}, nil
}

func validateReasoningRequest(baseModel string, requested *api.ReasoningConfig) error {
	if requested == nil {
		return nil
	}
	if err := requested.Validate(); err != nil {
		return &ReasoningRequestError{BaseModel: baseModel, Reason: err.Error()}
	}
	return nil
}

func resolveUndeclaredReasoning(
	baseModel string,
	requested *api.ReasoningConfig,
) (*api.ReasoningConfig, error) {
	if requested == nil {
		return nil, nil
	}
	return nil, &ReasoningUnsupportedError{
		BaseModel: baseModel,
		Thinking:  requested.Thinking,
		Effort:    requested.Effort,
		Reason:    "no reasoning capability is declared",
	}
}

func selectReasoningMode(
	baseModel string,
	policy *ReasoningPolicy,
	requested *api.ReasoningConfig,
) (api.ThinkingMode, *ThinkingModePolicy, error) {
	thinking := defaultThinking(policy, requested)
	selected := (*ThinkingModePolicy)(nil)
	if thinking == "" && requested != nil && requested.Effort != "" {
		var matches int
		selected, matches = modeForEffort(policy, requested.Effort)
		if matches == 0 {
			return "", nil, &ReasoningUnsupportedError{BaseModel: baseModel, Effort: requested.Effort, Reason: "a thinking mode is required to select this effort"}
		}
		if matches > 1 {
			return "", nil, &ReasoningRequestError{BaseModel: baseModel, Reason: fmt.Sprintf("effort %q belongs to multiple thinking modes; specify thinking", requested.Effort)}
		}
		thinking = selected.Thinking
	}
	if thinking == "" {
		return "", nil, nil
	}
	if selected == nil {
		selected = modeForThinking(policy, thinking)
	}
	if selected == nil {
		return "", nil, &ReasoningUnsupportedError{
			BaseModel: baseModel,
			Thinking:  thinking,
			Reason:    "thinking mode is not declared",
		}
	}
	return thinking, selected, nil
}

func defaultThinking(policy *ReasoningPolicy, requested *api.ReasoningConfig) api.ThinkingMode {
	if requested != nil && requested.Thinking != "" {
		return requested.Thinking
	}
	if policy.DefaultThinking != nil {
		return *policy.DefaultThinking
	}
	return ""
}

func modeForEffort(policy *ReasoningPolicy, effort api.ReasoningEffort) (*ThinkingModePolicy, int) {
	var selected *ThinkingModePolicy
	matches := 0
	for index := range policy.ThinkingModes {
		mode := &policy.ThinkingModes[index]
		if slices.Contains(mode.Efforts, effort) {
			selected = mode
			matches++
		}
	}
	return selected, matches
}

func modeForThinking(policy *ReasoningPolicy, thinking api.ThinkingMode) *ThinkingModePolicy {
	for index := range policy.ThinkingModes {
		if policy.ThinkingModes[index].Thinking == thinking {
			return &policy.ThinkingModes[index]
		}
	}
	return nil
}

func resolveRequestedEffort(
	modePolicy *ThinkingModePolicy,
	requested *api.ReasoningConfig,
) api.ReasoningEffort {
	var effort api.ReasoningEffort
	if modePolicy != nil && modePolicy.DefaultEffort != nil {
		effort = *modePolicy.DefaultEffort
	}
	if requested != nil && requested.Effort != "" {
		effort = requested.Effort
	}
	return effort
}

func validateResolvedReasoning(
	baseModel string,
	modePolicy *ThinkingModePolicy,
	thinking api.ThinkingMode,
	effort api.ReasoningEffort,
) error {
	if effort == "" {
		return nil
	}
	if thinking == api.ThinkingDisabled {
		return &ReasoningRequestError{
			BaseModel: baseModel,
			Reason:    "reasoning effort cannot be set when thinking is disabled",
		}
	}
	if slices.Contains(modePolicy.Efforts, effort) {
		return nil
	}
	return &ReasoningUnsupportedError{
		BaseModel: baseModel,
		Thinking:  thinking,
		Effort:    effort,
		Reason:    "effort is not declared for this thinking mode",
	}
}

func resolveNumeric[T number](policy *NumericParameter[T], requested *T) *T {
	if policy == nil {
		return nil
	}
	value := policy.Default
	if requested != nil {
		value = requested
	}
	if value == nil {
		return nil
	}
	resolved := *value
	if policy.Minimum != nil && resolved < *policy.Minimum {
		resolved = *policy.Minimum
	}
	if policy.Maximum != nil && resolved > *policy.Maximum {
		resolved = *policy.Maximum
	}
	return &resolved
}

func resolveOutputTokens(
	policy *OutputTokenParameter,
	requested *int64,
	minimum int64,
	maximum int64,
) *int64 {
	if policy == nil {
		return nil
	}
	resolved := gatewayDefaultMaxOutputTokens
	if requested != nil {
		resolved = *requested
	}
	if minimum > 0 && resolved < minimum {
		resolved = minimum
	}
	if maximum > 0 && resolved > maximum {
		resolved = maximum
	}
	return &resolved
}

func resolveStop(policy *StopParameter, requested []string) []string {
	if policy == nil {
		return nil
	}
	value := policy.Default
	if requested != nil {
		value = requested
	}
	if value == nil {
		return nil
	}
	return append([]string{}, value...)
}

func normalizeGenerationPolicy(policy *GenerationPolicy) (*GenerationPolicy, string, string) {
	if policy == nil {
		return nil, "", ""
	}
	normalized := cloneGenerationPolicy(policy)
	if field, reason := validateFloatParameter(normalized.Temperature, func(value float64) error {
		return (api.GenerationParameters{Temperature: &value}).Validate()
	}); reason != "" {
		return nil, "temperature." + field, reason
	}
	if field, reason := validateFloatParameter(normalized.TopP, func(value float64) error {
		return (api.GenerationParameters{TopP: &value}).Validate()
	}); reason != "" {
		return nil, "top_p." + field, reason
	}
	if field, reason := validateNumericParameter(normalized.Seed); reason != "" {
		return nil, "seed." + field, reason
	}
	if normalized.Stop != nil {
		if err := (api.GenerationParameters{Stop: normalized.Stop.Default}).Validate(); err != nil {
			return nil, "stop.default", err.Error()
		}
	}
	return normalized, "", ""
}

func normalizeReasoningPolicy(policy *ReasoningPolicy) (*ReasoningPolicy, string, string) {
	if policy == nil {
		return nil, "", ""
	}
	normalized := cloneReasoningPolicy(policy)
	if field, reason := normalizeThinkingModes(normalized); reason != "" {
		return nil, field, reason
	}
	slices.SortFunc(normalized.ThinkingModes, compareThinkingModes)
	return normalized, "", ""
}

func normalizeThinkingModes(policy *ReasoningPolicy) (string, string) {
	if len(policy.ThinkingModes) == 0 {
		return "thinking_modes", "must not be empty"
	}
	seen := make(map[api.ThinkingMode]struct{}, len(policy.ThinkingModes))
	for index := range policy.ThinkingModes {
		mode := &policy.ThinkingModes[index]
		if !mode.Thinking.IsValid() {
			return fmt.Sprintf("thinking_modes[%d].thinking", index), fmt.Sprintf("unsupported value %q", mode.Thinking)
		}
		if _, exists := seen[mode.Thinking]; exists {
			return "thinking_modes", fmt.Sprintf("duplicate thinking mode %q", mode.Thinking)
		}
		seen[mode.Thinking] = struct{}{}
		if field, reason := normalizeThinkingMode(index, mode); reason != "" {
			return field, reason
		}
	}
	if policy.DefaultThinking != nil {
		if !policy.DefaultThinking.IsValid() {
			return "default_thinking", fmt.Sprintf("unsupported value %q", *policy.DefaultThinking)
		}
		if _, declared := seen[*policy.DefaultThinking]; !declared {
			return "default_thinking", "must refer to a declared thinking mode"
		}
	}
	return "", ""
}

func normalizeThinkingMode(index int, mode *ThinkingModePolicy) (string, string) {
	var err error
	mode.Efforts, err = normalizeSet(mode.Efforts, api.ReasoningEffort.IsValid)
	if err != nil {
		return fmt.Sprintf("thinking_modes[%d].efforts", index), err.Error()
	}
	if mode.Thinking == api.ThinkingDisabled && len(mode.Efforts) != 0 {
		return fmt.Sprintf("thinking_modes[%d].efforts", index), "disabled thinking must not declare efforts"
	}
	return validateDefaultEffort(index, mode)
}

func validateDefaultEffort(index int, mode *ThinkingModePolicy) (string, string) {
	if mode.DefaultEffort == nil {
		return "", ""
	}
	field := fmt.Sprintf("thinking_modes[%d].default_effort", index)
	if mode.Thinking == api.ThinkingDisabled {
		return field, "disabled thinking must not declare a default effort"
	}
	if !slices.Contains(mode.Efforts, *mode.DefaultEffort) {
		return field, "must be one of the declared efforts"
	}
	return "", ""
}

func compareThinkingModes(left, right ThinkingModePolicy) int {
	if left.Thinking < right.Thinking {
		return -1
	}
	if left.Thinking > right.Thinking {
		return 1
	}
	return 0
}

func normalizeEmbeddingPolicy(policy *EmbeddingPolicy) (*EmbeddingPolicy, string, string) {
	if policy == nil {
		return nil, "", ""
	}
	normalized := cloneEmbeddingPolicy(policy)
	if normalized.FixedDimensions != nil && normalized.Dimensions != nil {
		return nil, "dimensions", "fixed and configurable dimensions are mutually exclusive"
	}
	if normalized.FixedDimensions != nil && *normalized.FixedDimensions < 1 {
		return nil, "fixed_dimensions", "must be positive"
	}
	if field, reason := validateNumericParameter(normalized.Dimensions); reason != "" {
		return nil, "dimensions." + field, reason
	}
	dimensions := NumericParameter[int64]{}
	if normalized.Dimensions != nil {
		dimensions = *normalized.Dimensions
	}
	for _, candidate := range [...]struct {
		field string
		value *int64
	}{
		{field: "fixed_dimensions", value: normalized.FixedDimensions},
		{field: "dimensions.minimum", value: dimensions.Minimum},
		{field: "dimensions.maximum", value: dimensions.Maximum},
		{field: "dimensions.default", value: dimensions.Default},
	} {
		if candidate.value != nil && *candidate.value < 1 {
			return nil, candidate.field, "must be positive"
		}
	}
	return normalized, "", ""
}

func validateFloatParameter(policy *NumericParameter[float64], validate func(float64) error) (string, string) {
	if policy == nil {
		return "", ""
	}
	for _, candidate := range [...]struct {
		field string
		value *float64
	}{
		{field: "minimum", value: policy.Minimum},
		{field: "maximum", value: policy.Maximum},
		{field: "default", value: policy.Default},
	} {
		if candidate.value != nil {
			if err := validate(*candidate.value); err != nil {
				return candidate.field, err.Error()
			}
		}
	}
	return validateNumericParameter(policy)
}

func validateNumericParameter[T number](policy *NumericParameter[T]) (string, string) {
	if policy == nil {
		return "", ""
	}
	if policy.Minimum != nil && policy.Maximum != nil && *policy.Minimum > *policy.Maximum {
		return "bounds", "minimum must not exceed maximum"
	}
	if policy.Default != nil && policy.Minimum != nil && *policy.Default < *policy.Minimum {
		return "default", "must not be below the configured minimum"
	}
	if policy.Default != nil && policy.Maximum != nil && *policy.Default > *policy.Maximum {
		return "default", "must not exceed the configured maximum"
	}
	return "", ""
}

func cloneGenerationPolicy(policy *GenerationPolicy) *GenerationPolicy {
	if policy == nil {
		return nil
	}
	cloned := *policy
	cloned.Temperature = cloneNumericParameter(policy.Temperature)
	cloned.TopP = cloneNumericParameter(policy.TopP)
	if policy.Stop != nil {
		stop := *policy.Stop
		if stop.Default != nil {
			stop.Default = append([]string{}, stop.Default...)
		}
		cloned.Stop = &stop
	}
	cloned.Seed = cloneNumericParameter(policy.Seed)
	return &cloned
}

func cloneReasoningPolicy(policy *ReasoningPolicy) *ReasoningPolicy {
	if policy == nil {
		return nil
	}
	cloned := &ReasoningPolicy{
		ThinkingModes:   make([]ThinkingModePolicy, len(policy.ThinkingModes)),
		DefaultThinking: clonePointer(policy.DefaultThinking),
	}
	for index, mode := range policy.ThinkingModes {
		cloned.ThinkingModes[index] = ThinkingModePolicy{
			Thinking:      mode.Thinking,
			Efforts:       append([]api.ReasoningEffort(nil), mode.Efforts...),
			DefaultEffort: clonePointer(mode.DefaultEffort),
		}
	}
	return cloned
}

func cloneEmbeddingPolicy(policy *EmbeddingPolicy) *EmbeddingPolicy {
	if policy == nil {
		return nil
	}
	return &EmbeddingPolicy{
		FixedDimensions: clonePointer(policy.FixedDimensions),
		Dimensions:      cloneNumericParameter(policy.Dimensions),
	}
}

func cloneNumericParameter[T number](policy *NumericParameter[T]) *NumericParameter[T] {
	if policy == nil {
		return nil
	}
	return &NumericParameter[T]{
		Minimum: clonePointer(policy.Minimum),
		Maximum: clonePointer(policy.Maximum),
		Default: clonePointer(policy.Default),
	}
}

func clonePointer[T any](value *T) *T {
	if value == nil {
		return nil
	}
	cloned := *value
	return &cloned
}
