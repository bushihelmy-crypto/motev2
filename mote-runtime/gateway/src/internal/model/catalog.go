package model

import (
	"bytes"
	"compress/gzip"
	_ "embed"
	"encoding/json"
	"fmt"
	"io"
)

//go:embed catalog_data.json.gz
var catalogData []byte

// CatalogSchemaVersion identifies the embedded model-definition document.
// The document is a static seed owned by Gateway; it has no upstream-source
// provenance or importer-specific fields.
const CatalogSchemaVersion = 3

// CatalogDocument is the typed durable model-catalog artifact.
type CatalogDocument struct {
	SchemaVersion int      `json:"schema_version"`
	Models        []Config `json:"models"`
}

// Catalog is an immutable exact-BaseModel registry. Construction resolves each
// Kernel override once; lookup never aliases, routes, discovers, refreshes, or
// falls back to another model.
type Catalog struct {
	definitions map[string]Definition
}

// ModelNotFoundError reports that Router selected an exact BaseModel absent
// from the configured catalog. Lookup never tries another spelling or model.
type ModelNotFoundError struct {
	BaseModel string
}

func (err *ModelNotFoundError) Error() string {
	return fmt.Sprintf("model %q is not present in the catalog", err.BaseModel)
}

// CatalogDataError reports an invalid embedded catalog document rather than
// hiding a release artifact failure as an individual model configuration error.
type CatalogDataError struct {
	Reason string
}

func (err *CatalogDataError) Error() string {
	return "invalid embedded model catalog: " + err.Reason
}

// NewCatalog loads Gateway's embedded model seed and applies each Kernel
// override exactly once. An override for an unknown BaseModel defines a custom
// model through the same validation path and must provide its complete
// capability.
func NewCatalog(overrides []Override) (Catalog, error) {
	defaults, err := loadCatalogDefaults(catalogData)
	if err != nil {
		return Catalog{}, err
	}
	return newCatalog(defaults, overrides)
}

func newCatalog(defaults []Config, overrides []Override) (Catalog, error) {
	definitions := make(map[string]Definition, len(defaults)+len(overrides))
	for _, candidate := range defaults {
		normalized, err := normalizeConfig(candidate)
		if err != nil {
			return Catalog{}, err
		}
		if _, exists := definitions[normalized.BaseModel]; exists {
			return Catalog{}, configError(normalized.BaseModel, "base_model", "duplicates another catalog model")
		}
		definitions[normalized.BaseModel] = Definition{config: normalized}
	}

	seen := make(map[string]struct{}, len(overrides))
	for _, override := range overrides {
		if override.BaseModel == "" {
			return Catalog{}, configError(override.BaseModel, "override.base_model", "must not be empty")
		}
		if _, exists := seen[override.BaseModel]; exists {
			return Catalog{}, configError(override.BaseModel, "override.base_model", "duplicates another override")
		}
		seen[override.BaseModel] = struct{}{}

		base, exists := definitions[override.BaseModel]
		if !exists {
			base = Definition{config: Config{BaseModel: override.BaseModel, Lifecycle: LifecycleActive}}
		}
		patched, err := applyOverride(base.config, override)
		if err != nil {
			return Catalog{}, err
		}
		definitions[override.BaseModel] = Definition{config: patched}
	}

	return Catalog{definitions: definitions}, nil
}

func loadCatalogDefaults(data []byte) ([]Config, error) {
	decoded, err := decodeCatalogData(data)
	if err != nil {
		return nil, &CatalogDataError{Reason: err.Error()}
	}
	decoder := json.NewDecoder(bytes.NewReader(decoded))
	decoder.DisallowUnknownFields()
	var document CatalogDocument
	if err := decoder.Decode(&document); err != nil {
		return nil, &CatalogDataError{Reason: err.Error()}
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return nil, &CatalogDataError{Reason: "must contain exactly one JSON document"}
	}
	if document.SchemaVersion != CatalogSchemaVersion {
		return nil, &CatalogDataError{Reason: fmt.Sprintf("unsupported schema version %d", document.SchemaVersion)}
	}
	if len(document.Models) == 0 {
		return nil, &CatalogDataError{Reason: "models must not be empty"}
	}
	return document.Models, nil
}

func decodeCatalogData(data []byte) ([]byte, error) {
	if len(data) < 2 || data[0] != 0x1f || data[1] != 0x8b {
		return data, nil
	}
	reader, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		return nil, fmt.Errorf("decode gzip artifact: %w", err)
	}
	decoded, readErr := io.ReadAll(reader)
	closeErr := reader.Close()
	if readErr != nil {
		return nil, fmt.Errorf("read gzip artifact: %w", readErr)
	}
	if closeErr != nil {
		return nil, fmt.Errorf("close gzip artifact: %w", closeErr)
	}
	return decoded, nil
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
