package ports

import (
	"context"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

// ModelCatalogSource is the read-only persistence boundary for the complete
// model catalog. Production composition must provide the database-backed
// implementation; the gateway never falls back to an embedded seed when a
// source fails.
type ModelCatalogSource interface {
	LoadModelCatalog(context.Context) ([]ModelRecord, error)
}

// ModelRecord is the public persistence record converted by internal/model.
// It intentionally contains only durable model facts and no protocol, service,
// endpoint, credential, routing, or pricing fields.
type ModelRecord struct {
	BaseModel   string           `json:"base_model"`
	Lifecycle   ModelLifecycle   `json:"lifecycle"`
	TokenLimits ModelTokenLimits `json:"token_limits"`
	Capability  ModelCapability  `json:"capability"`
}

// ModelLifecycle is kept at the persistence boundary so ports does not expose
// an internal/model type.
type ModelLifecycle string

const (
	ModelLifecycleActive     ModelLifecycle = "active"
	ModelLifecycleDeprecated ModelLifecycle = "deprecated"
	ModelLifecycleRetired    ModelLifecycle = "retired"
)

type ModelTokenLimits struct {
	ContextWindowTokens int64 `json:"context_window_tokens,omitempty"`
	MaxInputTokens      int64 `json:"max_input_tokens,omitempty"`
	MinOutputTokens     int64 `json:"min_output_tokens,omitempty"`
	MaxOutputTokens     int64 `json:"max_output_tokens,omitempty"`
}

type ModelCapability struct {
	Operation        api.Operation    `json:"operation"`
	InputModalities  []api.Modality   `json:"input_modalities"`
	OutputModalities []api.Modality   `json:"output_modalities"`
	Features         []api.Feature    `json:"features,omitempty"`
	Generation       *ModelGeneration `json:"generation,omitempty"`
	Embedding        *ModelEmbedding  `json:"embedding,omitempty"`
	Reasoning        *ModelReasoning  `json:"reasoning,omitempty"`
}

type ModelGeneration struct {
	Temperature     *ModelNumericFloat `json:"temperature,omitempty"`
	TopP            *ModelNumericFloat `json:"top_p,omitempty"`
	MaxOutputTokens *ModelOutputTokens `json:"max_output_tokens,omitempty"`
	Stop            *ModelStop         `json:"stop,omitempty"`
	Seed            *ModelNumericInt   `json:"seed,omitempty"`
}

// ModelOutputTokens is a presence marker for model support of the
// max_output_tokens request control.
type ModelOutputTokens struct{}

type ModelNumericFloat struct {
	Minimum *float64 `json:"minimum,omitempty"`
	Maximum *float64 `json:"maximum,omitempty"`
	Default *float64 `json:"default,omitempty"`
}

type ModelNumericInt struct {
	Minimum *int64 `json:"minimum,omitempty"`
	Maximum *int64 `json:"maximum,omitempty"`
	Default *int64 `json:"default,omitempty"`
}

type ModelStop struct {
	Default []string `json:"default,omitempty"`
}

type ModelEmbedding struct {
	FixedDimensions *int64           `json:"fixed_dimensions,omitempty"`
	Dimensions      *ModelNumericInt `json:"dimensions,omitempty"`
}

type ModelReasoning struct {
	ThinkingModes   []ModelThinkingMode `json:"thinking_modes"`
	DefaultThinking *api.ThinkingMode   `json:"default_thinking,omitempty"`
}

type ModelThinkingMode struct {
	Thinking      api.ThinkingMode      `json:"thinking"`
	Efforts       []api.ReasoningEffort `json:"efforts,omitempty"`
	DefaultEffort *api.ReasoningEffort  `json:"default_effort,omitempty"`
}
