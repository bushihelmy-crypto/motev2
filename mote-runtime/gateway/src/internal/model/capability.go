package model

import (
	"math"
	"slices"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

type number interface {
	~float64 | ~int64
}

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

// EmbeddingPolicy owns only embedding-specific controls. FixedDimensions is a
// fixed output width and filters a requested dimensions value. Dimensions is
// non-nil only when the model lets the caller choose the width. The two forms
// are mutually exclusive so the default has one owner.
type EmbeddingPolicy struct {
	FixedDimensions *int64                   `json:"fixed_dimensions,omitempty"`
	Dimensions      *NumericParameter[int64] `json:"dimensions,omitempty"`
}

// OperationConfig describes one operation a model can perform. Differences
// between model configurations are expressed as data here, not behavioral
// subclasses. Generation and Embedding are operation-specific typed policies.
type OperationConfig struct {
	Operation        api.Operation      `json:"operation"`
	Modes            []api.DeliveryMode `json:"modes"`
	InputModalities  []api.Modality     `json:"input_modalities"`
	OutputModalities []api.Modality     `json:"output_modalities"`
	Features         []api.Feature      `json:"features,omitempty"`
	Generation       *GenerationPolicy  `json:"generation,omitempty"`
	Embedding        *EmbeddingPolicy   `json:"embedding,omitempty"`
}

// Capability is an immutable view of one normalized OperationConfig.
type Capability struct {
	definition     Definition
	operationIndex int
}

// SupportsMode reports whether this operation supports the delivery mode.
func (capability Capability) SupportsMode(mode api.DeliveryMode) bool {
	operation := capability.definition.config.Operations[capability.operationIndex]
	return slices.Contains(operation.Modes, mode)
}

// SupportsInputModality reports whether this operation accepts the modality.
func (capability Capability) SupportsInputModality(modality api.Modality) bool {
	operation := capability.definition.config.Operations[capability.operationIndex]
	return slices.Contains(operation.InputModalities, modality)
}

// SupportsOutputModality reports whether this operation produces the modality.
func (capability Capability) SupportsOutputModality(modality api.Modality) bool {
	operation := capability.definition.config.Operations[capability.operationIndex]
	return slices.Contains(operation.OutputModalities, modality)
}

// SupportsFeature reports whether this operation supports the required model
// feature. Unsupported required features are rejected by admission rather than
// silently removed.
func (capability Capability) SupportsFeature(feature api.Feature) bool {
	operation := capability.definition.config.Operations[capability.operationIndex]
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
		definition.Operations[capability.operationIndex].Generation,
		requested,
		definition.TokenLimits,
	)
}

// ResolveEmbeddingDimensions applies an adjustable embedding dimension default
// and explicit request, clamping it to known bounds. It returns nil for a fixed
// width or a model that does not support the dimensions request parameter.
func (capability Capability) ResolveEmbeddingDimensions(requested *int64) *int64 {
	operation := capability.definition.config.Operations[capability.operationIndex]
	if operation.Embedding == nil {
		return nil
	}
	return resolveNumeric(operation.Embedding.Dimensions, requested)
}

// DefaultEmbeddingDimensions returns a known fixed or adjustable default
// output width. False means the catalog has no authoritative width.
func (capability Capability) DefaultEmbeddingDimensions() (int64, bool) {
	operation := capability.definition.config.Operations[capability.operationIndex]
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
	defaultValue := gatewayDefaultMaxOutputTokens
	value := &defaultValue
	if requested != nil {
		value = requested
	}
	if value == nil {
		return nil
	}
	resolved := *value
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
	if field, reason := validateFloatParameter(normalized.Temperature); reason != "" {
		return nil, "temperature." + field, reason
	}
	if field, reason := validateFloatParameter(normalized.TopP); reason != "" {
		return nil, "top_p." + field, reason
	}
	if field, reason := validateNumericParameter(normalized.Seed); reason != "" {
		return nil, "seed." + field, reason
	}
	return normalized, "", ""
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

func validateFloatParameter(policy *NumericParameter[float64]) (string, string) {
	if policy == nil {
		return "", ""
	}
	for _, value := range []*float64{policy.Minimum, policy.Maximum, policy.Default} {
		if value != nil && (math.IsNaN(*value) || math.IsInf(*value, 0)) {
			return "value", "must be finite"
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
