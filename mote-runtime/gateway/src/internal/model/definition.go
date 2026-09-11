package model

import (
	"cmp"
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

	// gatewayDefaultMaxOutputTokens is a Gateway policy, not a
	// provider/model fact. A model's own minimum and maximum remain owned by
	// TokenLimits.
	gatewayDefaultMaxOutputTokens   int64 = 4096
	operationEmbeddingMismatchError       = "non-embedding operation must not declare embedding parameters"
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
	ID          string            `json:"id"`
	Lifecycle   Lifecycle         `json:"lifecycle"`
	TokenLimits TokenLimits       `json:"token_limits"`
	Operations  []OperationConfig `json:"operations"`
}

// Override is the single model configuration patch delivered by Kernel.
// Scalar pointers inherit when absent. A nil Operations slice inherits the
// built-in operation set; a non-nil slice replaces it completely. For a custom
// model absent from the built-in catalog, Operations must define the model.
type Override struct {
	ModelID     string
	Lifecycle   *Lifecycle
	TokenLimits TokenLimitsOverride
	Operations  []OperationConfig
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

func (err *ConfigError) Error() string {
	return fmt.Sprintf("invalid model config %q %s: %s", err.ModelID, err.Field, err.Reason)
}

// ID returns the exact catalog model identifier selected by Router.
func (definition Definition) ID() string {
	return definition.config.ID
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
	for index, config := range definition.config.Operations {
		if config.Operation == operation {
			return Capability{
				definition:     definition,
				operationIndex: index,
			}, true
		}
	}
	return Capability{}, false
}

func normalizeConfig(config Config) (Config, error) {
	if config.ID == "" {
		return Config{}, configError(config.ID, "id", "must not be empty")
	}
	if config.Lifecycle != LifecycleActive && config.Lifecycle != LifecycleDeprecated && config.Lifecycle != LifecycleRetired {
		return Config{}, configError(config.ID, "lifecycle", fmt.Sprintf("unsupported value %q", config.Lifecycle))
	}
	limits := config.TokenLimits
	if reason := limits.validationError(); reason != "" {
		return Config{}, configError(config.ID, "token_limits", reason)
	}
	if len(config.Operations) == 0 {
		return Config{}, configError(config.ID, "operations", "must contain at least one operation")
	}

	operations := make([]OperationConfig, 0, len(config.Operations))
	seen := make(map[api.Operation]struct{}, len(config.Operations))
	for _, candidate := range config.Operations {
		operation, err := normalizeOperationConfig(config.ID, candidate)
		if err != nil {
			return Config{}, err
		}
		if _, exists := seen[operation.Operation]; exists {
			return Config{}, configError(config.ID, "operations", fmt.Sprintf("duplicate operation %q", operation.Operation))
		}
		seen[operation.Operation] = struct{}{}
		operations = append(operations, operation)
	}
	slices.SortFunc(operations, func(left, right OperationConfig) int {
		return cmp.Compare(left.Operation, right.Operation)
	})
	config.Operations = operations
	return config, nil
}

func normalizeOperationConfig(modelID string, config OperationConfig) (OperationConfig, error) {
	if !config.Operation.IsValid() {
		return OperationConfig{}, configError(modelID, "operations.operation", fmt.Sprintf("unsupported value %q", config.Operation))
	}
	if len(config.Modes) == 0 || len(config.InputModalities) == 0 || len(config.OutputModalities) == 0 {
		return OperationConfig{}, configError(modelID, "operations", "modes and input/output modalities must not be empty")
	}
	var err error
	config.Modes, err = normalizeSet(config.Modes, api.DeliveryMode.IsValid)
	if err != nil {
		return OperationConfig{}, configError(modelID, "operations.modes", err.Error())
	}
	config.InputModalities, err = normalizeSet(config.InputModalities, api.Modality.IsValid)
	if err != nil {
		return OperationConfig{}, configError(modelID, "operations.input_modalities", err.Error())
	}
	config.OutputModalities, err = normalizeSet(config.OutputModalities, api.Modality.IsValid)
	if err != nil {
		return OperationConfig{}, configError(modelID, "operations.output_modalities", err.Error())
	}
	config.Features, err = normalizeSet(config.Features, api.Feature.IsValid)
	if err != nil {
		return OperationConfig{}, configError(modelID, "operations.features", err.Error())
	}
	if reason := operationPolicyError(config); reason != "" {
		return OperationConfig{}, configError(modelID, "operations", reason)
	}

	generation, field, reason := normalizeGenerationPolicy(config.Generation)
	if reason != "" {
		return OperationConfig{}, configError(modelID, "operations.generation."+field, reason)
	}
	config.Generation = generation
	embedding, field, reason := normalizeEmbeddingPolicy(config.Embedding)
	if reason != "" {
		return OperationConfig{}, configError(modelID, "operations.embedding."+field, reason)
	}
	config.Embedding = embedding
	return config, nil
}

// operationPolicies is the one operation-shape rule table. It is read-only;
// callers cannot mutate it because the package exposes no map accessor.
var operationPolicies = map[api.Operation]struct {
	requiredOutput  api.Modality
	exactOutput     bool
	embedding       bool
	embeddingError  string
	generationError string
}{
	api.OperationGenerate:           {embeddingError: operationEmbeddingMismatchError},
	api.OperationEmbedding:          {exactOutput: true, embedding: true, embeddingError: "embedding must declare its embedding capability", generationError: "embedding must not declare generation parameters"},
	api.OperationRerank:             {requiredOutput: api.ModalityText, generationError: "rerank must not declare generation parameters", embeddingError: operationEmbeddingMismatchError},
	api.OperationImageGeneration:    {requiredOutput: api.ModalityImage, embeddingError: operationEmbeddingMismatchError},
	api.OperationAudioGeneration:    {requiredOutput: api.ModalityAudio, embeddingError: operationEmbeddingMismatchError},
	api.OperationAudioTranscription: {requiredOutput: api.ModalityText, embeddingError: operationEmbeddingMismatchError},
	api.OperationMusicGeneration:    {requiredOutput: api.ModalityMusic, embeddingError: operationEmbeddingMismatchError},
	api.OperationVideoGeneration:    {requiredOutput: api.ModalityVideo, embeddingError: operationEmbeddingMismatchError},
	api.OperationRealtime:           {embeddingError: operationEmbeddingMismatchError},
}

func operationPolicyError(config OperationConfig) string {
	if slices.Contains(config.InputModalities, api.ModalityEmbedding) {
		return "embedding cannot be an operation input modality"
	}
	if config.Operation == api.OperationRealtime {
		if reason := realtimeOperationPolicyError(config); reason != "" {
			return reason
		}
	} else if slices.Contains(config.Modes, api.ModeDuplex) {
		return "only realtime may use duplex mode"
	}
	policy := operationPolicies[config.Operation]
	if policy.requiredOutput != "" && !slices.Contains(config.OutputModalities, policy.requiredOutput) {
		return fmt.Sprintf("%s must produce %s", config.Operation, policy.requiredOutput)
	}
	if policy.exactOutput && !slices.Equal(config.OutputModalities, []api.Modality{api.ModalityEmbedding}) {
		return "embedding must produce only the embedding modality"
	}
	if (config.Embedding != nil) != policy.embedding {
		return policy.embeddingError
	}
	if config.Generation != nil && policy.generationError != "" {
		return policy.generationError
	}
	if !policy.embedding && slices.Contains(config.OutputModalities, api.ModalityEmbedding) {
		return "non-embedding operation must not produce the embedding modality"
	}
	return ""
}

func realtimeOperationPolicyError(config OperationConfig) string {
	if !slices.Equal(config.Modes, []api.DeliveryMode{api.ModeDuplex}) {
		return "realtime must use duplex mode only"
	}
	if !slices.Contains(config.InputModalities, api.ModalityText) && !slices.Contains(config.InputModalities, api.ModalityAudio) {
		return "realtime must accept text or audio input"
	}
	if !slices.Contains(config.OutputModalities, api.ModalityText) && !slices.Contains(config.OutputModalities, api.ModalityAudio) {
		return "realtime must produce text or audio output"
	}
	return ""
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
	if override.Operations != nil {
		result.Operations = override.Operations
	}
	return normalizeConfig(result)
}

func configError(modelID, field, reason string) error {
	return &ConfigError{ModelID: modelID, Field: field, Reason: reason}
}
