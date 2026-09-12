package model

import (
	"fmt"
	"slices"
	"strings"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

// Lifecycle is the catalog state of a model definition.
type Lifecycle string

const (
	LifecycleActive     Lifecycle = "active"
	LifecycleDeprecated Lifecycle = "deprecated"
	LifecycleRetired    Lifecycle = "retired"

	// gatewayDefaultMaxOutputTokens is a Gateway policy, not a
	// model fact. A model's own minimum and maximum remain owned by TokenLimits;
	// Gateway policy must not be copied into this catalog field.
	gatewayDefaultMaxOutputTokens int64 = 4096
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

// TokenLimitsOverride patches known token limits. A nil pointer inherits the
// catalog value; a pointer to zero explicitly changes the value to unknown.
type TokenLimitsOverride struct {
	ContextWindowTokens *int64
	MaxInputTokens      *int64
	MinOutputTokens     *int64
	MaxOutputTokens     *int64
}

// Config is the common model configuration envelope. It deliberately contains
// no family grouping: Router owns every grouping used to select a model.
type Config struct {
	BaseModel   string           `json:"base_model"`
	Lifecycle   Lifecycle        `json:"lifecycle"`
	TokenLimits TokenLimits      `json:"token_limits"`
	Capability  CapabilityConfig `json:"capability"`
}

// Override is the single model configuration patch delivered by Kernel.
// Scalar pointers inherit when absent. A nil Capability inherits the built-in
// capability; a non-nil value replaces it completely. For a custom model
// absent from the built-in catalog, Capability must define the model.
type Override struct {
	BaseModel   string
	Lifecycle   *Lifecycle
	TokenLimits TokenLimitsOverride
	Capability  *CapabilityConfig
}

// Definition is the immutable model description consumed by admission and a
// call plan. It contains no service, protocol, endpoint, credential, routing,
// family, or pricing state.
type Definition struct {
	config Config
}

// ConfigError reports malformed catalog defaults or validated Kernel
// overrides. Field identifies the rejected model-owned configuration path.
type ConfigError struct {
	ModelID string
	Field   string
	Reason  string
}

// ValidateBaseModel enforces the catalog identity vocabulary. A BaseModel is
// an exact, bare model name; provider/service namespaces and surrounding
// whitespace belong to source records or invocation routing, never here.
func ValidateBaseModel(value string) error {
	if value == "" {
		return fmt.Errorf("must not be empty")
	}
	if strings.TrimSpace(value) != value {
		return fmt.Errorf("must not contain surrounding whitespace")
	}
	if strings.Contains(value, "/") {
		return fmt.Errorf("must be a bare model name without '/'")
	}
	return nil
}

func (err *ConfigError) Error() string {
	return fmt.Sprintf("invalid model config %q %s: %s", err.ModelID, err.Field, err.Reason)
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
	if reason := ValidateBaseModel(config.BaseModel); reason != nil {
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

// ValidateConfig applies the same immutable model validation used by runtime
// catalog construction. The source compiler calls this owner before publishing
// an artifact, so build-time and runtime cannot accept different shapes.
func ValidateConfig(config Config) error {
	_, err := normalizeConfig(config)
	return err
}

func normalizeCapabilityConfig(modelID string, config CapabilityConfig) (CapabilityConfig, error) {
	if !config.Operation.IsValid() {
		return CapabilityConfig{}, configError(modelID, "capability.operation", fmt.Sprintf("unsupported value %q", config.Operation))
	}
	if len(config.InputModalities) == 0 || len(config.OutputModalities) == 0 {
		return CapabilityConfig{}, configError(modelID, "capability", "input/output modalities must not be empty")
	}
	var err error
	config.InputModalities, err = normalizeSet(config.InputModalities, api.Modality.IsValid)
	if err != nil {
		return CapabilityConfig{}, configError(modelID, "capability.input_modalities", err.Error())
	}
	config.OutputModalities, err = normalizeSet(config.OutputModalities, api.Modality.IsValid)
	if err != nil {
		return CapabilityConfig{}, configError(modelID, "capability.output_modalities", err.Error())
	}
	config.Features, err = normalizeSet(config.Features, isModelFeature)
	if err != nil {
		return CapabilityConfig{}, configError(modelID, "capability.features", err.Error())
	}
	if err := validateCapabilityShape(capabilityShape{
		Operation:        config.Operation,
		InputModalities:  config.InputModalities,
		OutputModalities: config.OutputModalities,
		HasGeneration:    config.Generation != nil,
		HasEmbedding:     config.Embedding != nil,
	}); err != nil {
		return CapabilityConfig{}, configError(modelID, "capability", err.Error())
	}

	generation, field, reason := normalizeGenerationPolicy(config.Generation)
	if reason != "" {
		return CapabilityConfig{}, configError(modelID, "capability.generation."+field, reason)
	}
	config.Generation = generation
	embedding, field, reason := normalizeEmbeddingPolicy(config.Embedding)
	if reason != "" {
		return CapabilityConfig{}, configError(modelID, "capability.embedding."+field, reason)
	}
	config.Embedding = embedding
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

func applyOverride(base Config, override Override) (Config, error) {
	result := base
	if override.Lifecycle != nil {
		result.Lifecycle = *override.Lifecycle
	}
	if override.TokenLimits.ContextWindowTokens != nil {
		result.TokenLimits.ContextWindowTokens = *override.TokenLimits.ContextWindowTokens
	}
	if override.TokenLimits.MaxInputTokens != nil {
		result.TokenLimits.MaxInputTokens = *override.TokenLimits.MaxInputTokens
	}
	if override.TokenLimits.MinOutputTokens != nil {
		result.TokenLimits.MinOutputTokens = *override.TokenLimits.MinOutputTokens
	}
	if override.TokenLimits.MaxOutputTokens != nil {
		result.TokenLimits.MaxOutputTokens = *override.TokenLimits.MaxOutputTokens
	}
	if override.Capability != nil {
		result.Capability = *override.Capability
	}
	return normalizeConfig(result)
}

func configError(modelID, field, reason string) error {
	return &ConfigError{ModelID: modelID, Field: field, Reason: reason}
}
