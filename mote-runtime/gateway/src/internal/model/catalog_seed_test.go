package model

import (
	"bytes"
	"compress/gzip"
	_ "embed"
	"encoding/json"
	"fmt"
	"io"
)

// The checked-in catalog is test data only. Production construction accepts
// complete records exclusively through ports.ModelCatalogSource.
//
//go:embed testdata/catalog_data.json.gz
var catalogTestData []byte

const catalogTestSchemaVersion = 3

type catalogTestDocument struct {
	SchemaVersion int      `json:"schema_version"`
	Models        []Config `json:"models"`
}

func newSeedCatalog() (Catalog, error) {
	configs, err := loadCatalogTestData(catalogTestData)
	if err != nil {
		return Catalog{}, err
	}
	return newCatalog(configs)
}

func loadCatalogTestData(data []byte) ([]Config, error) {
	decoded, err := decodeCatalogTestData(data)
	if err != nil {
		return nil, &CatalogDataError{Reason: err.Error()}
	}
	decoder := json.NewDecoder(bytes.NewReader(decoded))
	decoder.DisallowUnknownFields()
	var document catalogTestDocument
	if err := decoder.Decode(&document); err != nil {
		return nil, &CatalogDataError{Reason: err.Error()}
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return nil, &CatalogDataError{Reason: "must contain exactly one JSON document"}
	}
	if document.SchemaVersion != catalogTestSchemaVersion {
		return nil, &CatalogDataError{Reason: fmt.Sprintf("unsupported schema version %d", document.SchemaVersion)}
	}
	if len(document.Models) == 0 {
		return nil, &CatalogDataError{Reason: "models must not be empty"}
	}
	return document.Models, nil
}

func decodeCatalogTestData(data []byte) ([]byte, error) {
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
