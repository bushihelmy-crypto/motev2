package main

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	modelcatalog "github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
)

func encodeCatalog(document modelcatalog.CatalogDocument) ([]byte, error) {
	var output bytes.Buffer
	fmt.Fprintf(&output, "{\n  \"schema_version\": %d,\n  \"sources\": ", document.SchemaVersion)
	sources, err := json.Marshal(document.Sources)
	if err != nil {
		return nil, fmt.Errorf("encode catalog sources: %w", err)
	}
	output.Write(sources)
	output.WriteString(",\n  \"models\": [\n")
	for index, model := range document.Models {
		if err := modelcatalog.ValidateBaseModel(model.BaseModel); err != nil {
			return nil, fmt.Errorf("encode model %q: BaseModel %w", model.BaseModel, err)
		}
		if err := modelcatalog.ValidateConfig(model); err != nil {
			return nil, fmt.Errorf("encode model %q: %w", model.BaseModel, err)
		}
		encoded, encodeErr := json.Marshal(model)
		if encodeErr != nil {
			return nil, fmt.Errorf("encode model %q: %w", model.BaseModel, encodeErr)
		}
		output.WriteString("    ")
		output.Write(encoded)
		if index+1 != len(document.Models) {
			output.WriteByte(',')
		}
		output.WriteByte('\n')
	}
	output.WriteString("  ]\n}\n")
	var compressed bytes.Buffer
	writer, err := gzip.NewWriterLevel(&compressed, gzip.BestCompression)
	if err != nil {
		return nil, fmt.Errorf("create catalog compressor: %w", err)
	}
	if _, err := writer.Write(output.Bytes()); err != nil {
		return nil, fmt.Errorf("compress catalog: %w", err)
	}
	if err := writer.Close(); err != nil {
		return nil, fmt.Errorf("close catalog compressor: %w", err)
	}
	return compressed.Bytes(), nil
}

// publishArtifact replaces the catalog in one atomic rename. Rejected source
// rows are diagnostics for this run only; no second artifact is persisted.
func publishArtifact(path string, data []byte) error {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return fmt.Errorf("resolve catalog output path: %w", err)
	}
	mode := os.FileMode(0o644)
	info, err := os.Lstat(absolute)
	if err == nil {
		if !info.Mode().IsRegular() {
			return fmt.Errorf("publish artifact %q: existing target is not a regular file", absolute)
		}
		mode = info.Mode().Perm()
	} else if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("inspect artifact %q: %w", absolute, err)
	}
	staged, err := stageArtifact(absolute, data, mode)
	if err != nil {
		return err
	}
	if err := os.Rename(staged, absolute); err != nil {
		_ = os.Remove(staged)
		return fmt.Errorf("replace catalog artifact %q: %w", absolute, err)
	}
	return nil
}

func stageArtifact(target string, data []byte, mode os.FileMode) (string, error) {
	directory := filepath.Dir(target)
	file, err := os.CreateTemp(directory, "."+filepath.Base(target)+".stage-*")
	if err != nil {
		return "", fmt.Errorf("stage artifact %q: %w", target, err)
	}
	path := file.Name()
	remove := true
	defer func() {
		if remove {
			_ = os.Remove(path)
		}
	}()
	if err := file.Chmod(mode); err != nil {
		_ = file.Close()
		return "", fmt.Errorf("set staged artifact mode %q: %w", target, err)
	}
	if _, err := file.Write(data); err != nil {
		_ = file.Close()
		return "", fmt.Errorf("write staged artifact %q: %w", target, err)
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return "", fmt.Errorf("sync staged artifact %q: %w", target, err)
	}
	if err := file.Close(); err != nil {
		return "", fmt.Errorf("close staged artifact %q: %w", target, err)
	}
	remove = false
	return path, nil
}
