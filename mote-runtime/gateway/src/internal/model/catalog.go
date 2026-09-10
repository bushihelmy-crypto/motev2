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

type catalogDocument struct {
	SchemaVersion int             `json:"schema_version"`
	Sources       []catalogSource `json:"sources"`
	Models        []Config        `json:"models"`
}

type catalogSource struct {
	Name     string `json:"name"`
	Revision string `json:"revision,omitempty"`
	SHA256   string `json:"sha256,omitempty"`
}

// Catalog is an immutable exact-ID model registry. Construction resolves each
// Kernel override once; lookup never aliases, routes, discovers, refreshes, or
// falls back to another model.
type Catalog struct {
	definitions map[string]Definition
}

// ModelNotFoundError reports that Router selected an exact model ID absent
// from the configured catalog. Lookup never tries another spelling or model.
type ModelNotFoundError struct {
	ModelID string
}

func (err *ModelNotFoundError) Error() string {
	return fmt.Sprintf("model %q is not present in the catalog", err.ModelID)
}

// CatalogDataError reports an invalid embedded catalog document rather than
// hiding a release artifact failure as an individual model configuration error.
type CatalogDataError struct {
	Reason string
}

func (err *CatalogDataError) Error() string {
	return "invalid embedded model catalog: " + err.Reason
}

// NewCatalog loads Gateway's built-in model defaults and applies each Kernel
// override exactly once. An override for an unknown ID defines a custom model
// through the same validation path and must provide its complete operations.
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
		if _, exists := definitions[normalized.ID]; exists {
			return Catalog{}, configError(normalized.ID, "id", "duplicates another catalog model")
		}
		definitions[normalized.ID] = Definition{config: normalized}
	}

	seen := make(map[string]struct{}, len(overrides))
	for _, override := range overrides {
		if override.ModelID == "" {
			return Catalog{}, configError(override.ModelID, "override.model_id", "must not be empty")
		}
		if _, exists := seen[override.ModelID]; exists {
			return Catalog{}, configError(override.ModelID, "override.model_id", "duplicates another override")
		}
		seen[override.ModelID] = struct{}{}

		base, exists := definitions[override.ModelID]
		if !exists {
			base = Definition{config: Config{ID: override.ModelID, Lifecycle: LifecycleActive}}
		}
		patched, err := applyOverride(base.config, override)
		if err != nil {
			return Catalog{}, err
		}
		definitions[override.ModelID] = Definition{config: patched}
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
	var document catalogDocument
	if err := decoder.Decode(&document); err != nil {
		return nil, &CatalogDataError{Reason: err.Error()}
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return nil, &CatalogDataError{Reason: "must contain exactly one JSON document"}
	}
	if document.SchemaVersion != 1 {
		return nil, &CatalogDataError{Reason: fmt.Sprintf("unsupported schema version %d", document.SchemaVersion)}
	}
	if len(document.Sources) == 0 {
		return nil, &CatalogDataError{Reason: "sources must not be empty"}
	}
	seenSources := make(map[string]struct{}, len(document.Sources))
	for _, source := range document.Sources {
		if source.Name == "" {
			return nil, &CatalogDataError{Reason: "source name must not be empty"}
		}
		if _, exists := seenSources[source.Name]; exists {
			return nil, &CatalogDataError{Reason: fmt.Sprintf("duplicate source %q", source.Name)}
		}
		seenSources[source.Name] = struct{}{}
		if (source.Revision == "") == (source.SHA256 == "") {
			return nil, &CatalogDataError{Reason: fmt.Sprintf("source %q must declare exactly one revision or SHA-256", source.Name)}
		}
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

// Lookup returns the exact model definition or a typed error. It never tries a
// family, prefix, alias, service, or fallback candidate.
func (catalog Catalog) Lookup(modelID string) (Definition, error) {
	definition, ok := catalog.definitions[modelID]
	if !ok {
		return Definition{}, &ModelNotFoundError{ModelID: modelID}
	}
	return definition, nil
}
