package model

import (
	"fmt"
	"slices"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

// Lifecycle is the catalog state of a model definition.
type Lifecycle string

const (
	LifecycleActive     Lifecycle = "active"
	LifecycleDeprecated Lifecycle = "deprecated"
	LifecycleRetired    Lifecycle = "retired"
)

// TokenLimits contains independently known model-native limits. Zero means
// the catalog does not know that bound; it never means unlimited.
type TokenLimits struct {
	ContextWindowTokens int64 `json:"context_window_tokens,omitempty"`
	MaxInputTokens      int64 `json:"max_input_tokens,omitempty"`
	MinOutputTokens     int64 `json:"min_output_tokens,omitempty"`
	MaxOutputTokens     int64 `json:"max_output_tokens,omitempty"`
}

func (limits TokenLimits) validationError() string {
	if min(limits.ContextWindowTokens, limits.MaxInputTokens, limits.MinOutputTokens, limits.MaxOutputTokens) < 0 {
		return "values must be non-negative"
	}
	if limits.MaxOutputTokens > 0 && limits.MinOutputTokens > limits.MaxOutputTokens {
		return "output minimum must not exceed output maximum"
	}
	if limits.ContextWindowTokens > 0 && max(limits.MaxInputTokens, limits.MinOutputTokens, limits.MaxOutputTokens) > limits.ContextWindowTokens {
		return "input and output maxima must not exceed the context window"
	}
	return ""
}

// Config is the common model configuration envelope. It deliberately contains
// no family grouping: Router owns every grouping used to select a model.
type Config struct {
	BaseModel   string           `json:"base_model"`
	Lifecycle   Lifecycle        `json:"lifecycle"`
	TokenLimits TokenLimits      `json:"token_limits"`
	Capability  CapabilityConfig `json:"capability"`
}

// Definition is the immutable model description consumed by admission and a
// admitted request. It contains no service, protocol, endpoint, credential, routing,
// family, or pricing state.
type Definition struct {
	config Config
}

// Operation returns the catalog operation for this model. Every catalog
// definition has exactly one operation; a request may omit the operation and
// let admission use this value as its default.
func (definition Definition) Operation() api.Operation {
	return definition.config.Capability.Operation
}

// ResolveOperation applies the request-level operation default. An empty
// request value means that the caller omitted the field. A non-empty value
// must match the operation declared by this model; Gateway never changes the
// selected model to satisfy a different operation.
func (definition Definition) ResolveOperation(requested *api.Operation) (api.Operation, error) {
	if requested == nil {
		return definition.Operation(), nil
	}
	if *requested == "" {
		return "", &OperationMismatchError{
			BaseModel: definition.BaseModel(),
			Requested: "",
			Available: definition.Operation(),
		}
	}
	if *requested != definition.Operation() {
		return "", &OperationMismatchError{
			BaseModel: definition.BaseModel(),
			Requested: *requested,
			Available: definition.Operation(),
		}
	}
	return *requested, nil
}

// ConfigError reports malformed model source data. Field identifies the
// rejected model-owned configuration path.
type ConfigError struct {
	BaseModel string
	Field     string
	Reason    string
}

// OperationMismatchError reports a request operation that does not match the
// operation declared by the selected model.
type OperationMismatchError struct {
	BaseModel string
	Requested api.Operation
	Available api.Operation
}

func (err *OperationMismatchError) Error() string {
	return fmt.Sprintf("model %q supports operation %q, not requested %q", err.BaseModel, err.Available, err.Requested)
}

func (err *ConfigError) Error() string {
	return fmt.Sprintf("invalid model config %q %s: %s", err.BaseModel, err.Field, err.Reason)
}

// BaseModel returns the exact model identity selected by Router.
func (definition Definition) BaseModel() string {
	return definition.config.BaseModel
}

// Lifecycle returns the model catalog lifecycle state.
func (definition Definition) Lifecycle() Lifecycle {
	return definition.config.Lifecycle
}

// TokenLimits returns a value copy of the model limits.
func (definition Definition) TokenLimits() TokenLimits {
	return definition.config.TokenLimits
}

// Capability returns an immutable operation capability when the model declares
// the operation.
func (definition Definition) Capability(operation api.Operation) (Capability, bool) {
	if definition.config.Capability.Operation != operation {
		return Capability{}, false
	}
	return Capability{definition: definition}, true
}

func normalizeConfig(config Config) (Config, error) {
	if reason := api.ValidateBaseModel(config.BaseModel); reason != nil {
		return Config{}, configError(config.BaseModel, "base_model", reason.Error())
	}
	if config.Lifecycle != LifecycleActive && config.Lifecycle != LifecycleDeprecated && config.Lifecycle != LifecycleRetired {
		return Config{}, configError(config.BaseModel, "lifecycle", fmt.Sprintf("unsupported value %q", config.Lifecycle))
	}
	limits := config.TokenLimits
	if reason := limits.validationError(); reason != "" {
		return Config{}, configError(config.BaseModel, "token_limits", reason)
	}
	capability, err := normalizeCapabilityConfig(config.BaseModel, config.Capability)
	if err != nil {
		return Config{}, err
	}
	config.Capability = capability
	return config, nil
}

func normalizeCapabilityConfig(baseModel string, config CapabilityConfig) (CapabilityConfig, error) {
	shapePolicy, ok := operationShapePolicies[config.Operation]
	if !ok {
		return CapabilityConfig{}, configError(baseModel, "capability.operation", fmt.Sprintf("unsupported value %q", config.Operation))
	}
	if len(config.InputModalities) == 0 || len(config.OutputModalities) == 0 {
		return CapabilityConfig{}, configError(baseModel, "capability", "input/output modalities must not be empty")
	}
	var err error
	config.InputModalities, err = normalizeSet(config.InputModalities, api.Modality.IsValid)
	if err != nil {
		return CapabilityConfig{}, configError(baseModel, "capability.input_modalities", err.Error())
	}
	config.OutputModalities, err = normalizeSet(config.OutputModalities, api.Modality.IsValid)
	if err != nil {
		return CapabilityConfig{}, configError(baseModel, "capability.output_modalities", err.Error())
	}
	config.Features, err = normalizeSet(config.Features, isModelFeature)
	if err != nil {
		return CapabilityConfig{}, configError(baseModel, "capability.features", err.Error())
	}
	if err := validateCapabilityShape(capabilityShape{
		Operation:        config.Operation,
		InputModalities:  config.InputModalities,
		OutputModalities: config.OutputModalities,
		HasGeneration:    config.Generation != nil,
		HasEmbedding:     config.Embedding != nil,
		HasReasoning:     config.Reasoning != nil,
	}, shapePolicy); err != nil {
		return CapabilityConfig{}, configError(baseModel, "capability", err.Error())
	}

	generation, field, reason := normalizeGenerationPolicy(config.Generation)
	if reason != "" {
		return CapabilityConfig{}, configError(baseModel, "capability.generation."+field, reason)
	}
	config.Generation = generation
	embedding, field, reason := normalizeEmbeddingPolicy(config.Embedding)
	if reason != "" {
		return CapabilityConfig{}, configError(baseModel, "capability.embedding."+field, reason)
	}
	config.Embedding = embedding
	reasoning, field, reason := normalizeReasoningPolicy(config.Reasoning)
	if reason != "" {
		return CapabilityConfig{}, configError(baseModel, "capability.reasoning."+field, reason)
	}
	config.Reasoning = reasoning
	return config, nil
}

func isModelFeature(feature api.Feature) bool {
	switch feature {
	case api.FeatureToolCalls, api.FeatureStructured:
		return true
	default:
		return false
	}
}

func normalizeSet[T ~string](values []T, valid func(T) bool) ([]T, error) {
	normalized := append([]T(nil), values...)
	slices.Sort(normalized)
	for index, value := range normalized {
		if !valid(value) {
			return nil, fmt.Errorf("contains unsupported value %q", value)
		}
		if index > 0 && value == normalized[index-1] {
			return nil, fmt.Errorf("contains duplicate value %q", value)
		}
	}
	return normalized, nil
}

func configError(baseModel, field, reason string) error {
	return &ConfigError{BaseModel: baseModel, Field: field, Reason: reason}
}
