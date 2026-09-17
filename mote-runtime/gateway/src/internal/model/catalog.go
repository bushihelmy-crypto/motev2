package model

import (
	"context"
	"fmt"
	"sync"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/ports"
)

// Catalog is an immutable exact-BaseModel registry. Construction resolves each
// complete source record once; lookup never aliases, routes, discovers, or
// falls back to another model.
type Catalog struct {
	definitions map[string]Definition
}

// CatalogReader is the immutable read surface admission uses. A plain
// Catalog implements it for tests and fixed composition; CatalogStore
// implements it for production refreshes without introducing a second
// admission path.
type CatalogReader interface {
	Current() Catalog
}

// Ready reports whether the catalog was constructed successfully. A zero or
// empty Catalog is deliberately not a permissive catalog: composition roots
// must inject a real immutable registry before admission can run.
func (catalog Catalog) Ready() bool { return len(catalog.definitions) > 0 }

// Current lets a fixed catalog satisfy CatalogReader without copying or
// mutating its definitions.
func (catalog Catalog) Current() Catalog { return catalog }

// NewCatalogFromRecords validates a complete durable catalog in one pass and
// returns an immutable in-memory value. No partial result is exposed when one
// record is invalid.
func NewCatalogFromRecords(records []ports.ModelRecord) (Catalog, error) {
	if len(records) == 0 {
		return Catalog{}, &CatalogDataError{Reason: "model source returned no records"}
	}
	defaults := make([]Config, len(records))
	for index, record := range records {
		defaults[index] = configFromRecord(record)
	}
	return newCatalog(defaults)
}

func configFromRecord(record ports.ModelRecord) Config {
	return Config{
		BaseModel: record.BaseModel,
		Lifecycle: Lifecycle(record.Lifecycle),
		TokenLimits: TokenLimits{
			ContextWindowTokens: record.TokenLimits.ContextWindowTokens,
			MaxInputTokens:      record.TokenLimits.MaxInputTokens,
			MinOutputTokens:     record.TokenLimits.MinOutputTokens,
			MaxOutputTokens:     record.TokenLimits.MaxOutputTokens,
		},
		Capability: capabilityFromRecord(record.Capability),
	}
}

func capabilityFromRecord(record ports.ModelCapability) CapabilityConfig {
	result := CapabilityConfig{
		Operation:        record.Operation,
		InputModalities:  append([]api.Modality(nil), record.InputModalities...),
		OutputModalities: append([]api.Modality(nil), record.OutputModalities...),
		Features:         append([]api.Feature(nil), record.Features...),
	}
	if record.Generation != nil {
		generation := &GenerationPolicy{}
		if value := record.Generation.Temperature; value != nil {
			generation.Temperature = &NumericParameter[float64]{Minimum: clonePointer(value.Minimum), Maximum: clonePointer(value.Maximum), Default: clonePointer(value.Default)}
		}
		if value := record.Generation.TopP; value != nil {
			generation.TopP = &NumericParameter[float64]{Minimum: clonePointer(value.Minimum), Maximum: clonePointer(value.Maximum), Default: clonePointer(value.Default)}
		}
		if record.Generation.MaxOutputTokens != nil {
			generation.MaxOutputTokens = &OutputTokenParameter{}
		}
		if value := record.Generation.Stop; value != nil {
			generation.Stop = &StopParameter{Default: append([]string(nil), value.Default...)}
		}
		if value := record.Generation.Seed; value != nil {
			generation.Seed = &NumericParameter[int64]{Minimum: clonePointer(value.Minimum), Maximum: clonePointer(value.Maximum), Default: clonePointer(value.Default)}
		}
		result.Generation = generation
	}
	if record.Embedding != nil {
		embedding := &EmbeddingPolicy{FixedDimensions: clonePointer(record.Embedding.FixedDimensions)}
		if value := record.Embedding.Dimensions; value != nil {
			embedding.Dimensions = &NumericParameter[int64]{Minimum: clonePointer(value.Minimum), Maximum: clonePointer(value.Maximum), Default: clonePointer(value.Default)}
		}
		result.Embedding = embedding
	}
	if record.Reasoning != nil {
		reasoning := &ReasoningPolicy{DefaultThinking: clonePointer(record.Reasoning.DefaultThinking), ThinkingModes: make([]ThinkingModePolicy, len(record.Reasoning.ThinkingModes))}
		for index, mode := range record.Reasoning.ThinkingModes {
			reasoning.ThinkingModes[index] = ThinkingModePolicy{Thinking: mode.Thinking, Efforts: append([]api.ReasoningEffort(nil), mode.Efforts...), DefaultEffort: clonePointer(mode.DefaultEffort)}
		}
		result.Reasoning = reasoning
	}
	return result
}

// CatalogStore owns the current immutable Catalog and its serialized refresh
// boundary. A failed read or validation leaves the previous catalog untouched.
type CatalogStore struct {
	source  ports.ModelCatalogSource
	refresh sync.Mutex
	current sync.RWMutex
	catalog Catalog
}

// NewCatalogStore loads the complete source before returning. A store without
// an initial valid catalog cannot be used for admission.
func NewCatalogStore(ctx context.Context, source ports.ModelCatalogSource) (*CatalogStore, error) {
	store := &CatalogStore{source: source}
	if err := store.Refresh(ctx); err != nil {
		return nil, err
	}
	return store, nil
}

// Current returns the latest complete immutable catalog.
func (store *CatalogStore) Current() Catalog {
	if store == nil {
		return Catalog{}
	}
	store.current.RLock()
	defer store.current.RUnlock()
	return store.catalog
}

// Refresh reads and validates a complete catalog, then swaps it atomically.
// Calls are serialized so a later refresh cannot be overwritten by an earlier
// in-flight load.
func (store *CatalogStore) Refresh(ctx context.Context) error {
	if store == nil || store.source == nil {
		return fmt.Errorf("model catalog source is required")
	}
	store.refresh.Lock()
	defer store.refresh.Unlock()
	records, err := store.source.LoadModelCatalog(ctx)
	if err != nil {
		return fmt.Errorf("load model catalog: %w", err)
	}
	next, err := NewCatalogFromRecords(records)
	if err != nil {
		return fmt.Errorf("validate model catalog: %w", err)
	}
	store.current.Lock()
	store.catalog = next
	store.current.Unlock()
	return nil
}

// ModelNotFoundError reports that Router selected an exact BaseModel absent
// from the configured catalog. Lookup never tries another spelling or model.
type ModelNotFoundError struct {
	BaseModel string
}

func (err *ModelNotFoundError) Error() string {
	return fmt.Sprintf("model %q is not present in the catalog", err.BaseModel)
}

// CatalogDataError reports invalid complete source data rather than exposing a
// partially validated model catalog.
type CatalogDataError struct {
	Reason string
}

func (err *CatalogDataError) Error() string {
	return "invalid model catalog: " + err.Reason
}

func newCatalog(configs []Config) (Catalog, error) {
	definitions := make(map[string]Definition, len(configs))
	for _, candidate := range configs {
		normalized, err := normalizeConfig(candidate)
		if err != nil {
			return Catalog{}, err
		}
		if _, exists := definitions[normalized.BaseModel]; exists {
			return Catalog{}, configError(normalized.BaseModel, "base_model", "duplicates another catalog model")
		}
		definitions[normalized.BaseModel] = Definition{config: normalized}
	}
	return Catalog{definitions: definitions}, nil
}

// Lookup returns the exact BaseModel definition or a typed error. It never tries a
// family, prefix, alias, service, or fallback candidate.
func (catalog Catalog) Lookup(baseModel string) (Definition, error) {
	definition, ok := catalog.definitions[baseModel]
	if !ok {
		return Definition{}, &ModelNotFoundError{BaseModel: baseModel}
	}
	return definition, nil
}
