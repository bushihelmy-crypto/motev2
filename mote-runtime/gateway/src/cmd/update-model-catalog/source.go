package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"sort"
	"strings"
)

const (
	defaultNewAPIRef  = "bdef1175" + "05247769" + "268b2096" + "65fb3ad7" + "554c3da7"
	defaultBifrostRef = "c5c02ae7" + "47fe294a" + "7f7f7652" + "77aae08b" + "bf0835fa"
)

type sourceRecord struct {
	// BaseModel is the authoritative pure-model identity. Source keys may carry
	// provider/service prefixes and are never published as model identities.
	BaseModel                   string            `json:"base_model"`
	Source                      string            `json:"source"`
	Mode                        string            `json:"mode"`
	MaxInputTokens              *json.Number      `json:"max_input_tokens"`
	MaxOutputTokens             *json.Number      `json:"max_output_tokens"`
	MaxTokens                   *json.Number      `json:"max_tokens"`
	OutputVectorSize            *json.Number      `json:"output_vector_size"`
	ModelParameters             []sourceParameter `json:"model_parameters"`
	SupportedModalities         []string          `json:"supported_modalities"`
	SupportedOutputModalities   []string          `json:"supported_output_modalities"`
	SupportsAudioInput          *bool             `json:"supports_audio_input"`
	SupportsAudioOutput         *bool             `json:"supports_audio_output"`
	SupportsEmbeddingImageInput *bool             `json:"supports_embedding_image_input"`
	SupportsFunctionCalling     *bool             `json:"supports_function_calling"`
	SupportsImageInput          *bool             `json:"supports_image_input"`
	// Structured-output flags are retained only as model compatibility evidence;
	// the protocol/service path still owns strict schema enforcement.
	SupportsNativeStructured *bool `json:"supports_native_structured_output"`
	SupportsResponseSchema   *bool `json:"supports_response_schema"`
	SupportsSamplingParams   *bool `json:"supports_sampling_params"`
	SupportsVideoInput       *bool `json:"supports_video_input"`
	SupportsVision           *bool `json:"supports_vision"`
	IsDeprecated             *bool `json:"is_deprecated"`
}

type sourceParameter struct {
	ID       string          `json:"id"`
	Disabled bool            `json:"disabled"`
	Default  json.RawMessage `json:"default"`
	Range    *sourceRange    `json:"range"`
}

type sourceRange struct {
	Minimum *json.Number `json:"min"`
	Maximum *json.Number `json:"max"`
}

type recordRef struct {
	key    string
	record sourceRecord
}

func decodeSourceRecords(data []byte) ([]recordRef, error) {
	if len(bytes.TrimSpace(data)) == 0 {
		return nil, errors.New("decode Bifrost model parameters: source snapshot is empty")
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	opening, err := decoder.Token()
	if err != nil {
		return nil, fmt.Errorf("decode Bifrost model parameters: %w", err)
	}
	if delimiter, ok := opening.(json.Delim); !ok || delimiter != '{' {
		return nil, errors.New("decode Bifrost model parameters: source snapshot must be a JSON object")
	}
	seen := make(map[string]struct{})
	records := make([]recordRef, 0)
	for decoder.More() {
		keyToken, keyErr := decoder.Token()
		if keyErr != nil {
			return nil, fmt.Errorf("decode Bifrost model parameters: read source record key: %w", keyErr)
		}
		key, ok := keyToken.(string)
		if !ok {
			return nil, errors.New("decode Bifrost model parameters: source record key must be a string")
		}
		if strings.TrimSpace(key) == "" {
			return nil, errors.New("decode Bifrost model parameters: source record key must not be empty")
		}
		if key != strings.TrimSpace(key) {
			return nil, fmt.Errorf("decode Bifrost model parameters: source record key %q must not contain surrounding whitespace", key)
		}
		if _, duplicate := seen[key]; duplicate {
			return nil, fmt.Errorf("decode Bifrost model parameters: duplicate source record key %q", key)
		}
		seen[key] = struct{}{}
		var record *sourceRecord
		if decodeErr := decoder.Decode(&record); decodeErr != nil {
			return nil, fmt.Errorf("decode Bifrost model parameters: source record %q: %w", key, decodeErr)
		}
		if record == nil {
			return nil, fmt.Errorf("decode Bifrost model parameters: source record %q must not be null", key)
		}
		records = append(records, recordRef{key: key, record: *record})
	}
	closing, closeErr := decoder.Token()
	if closeErr != nil {
		return nil, fmt.Errorf("decode Bifrost model parameters: close source object: %w", closeErr)
	}
	if delimiter, ok := closing.(json.Delim); !ok || delimiter != '}' {
		return nil, errors.New("decode Bifrost model parameters: source snapshot must be a JSON object")
	}
	if len(records) == 0 {
		return nil, errors.New("decode Bifrost model parameters: source snapshot must not be empty")
	}
	var trailing json.RawMessage
	if trailingErr := decoder.Decode(&trailing); trailingErr != io.EOF {
		if trailingErr == nil {
			return nil, errors.New("decode Bifrost model parameters: source snapshot must contain exactly one JSON document")
		}
		return nil, fmt.Errorf("decode Bifrost model parameters: trailing data: %w", trailingErr)
	}
	sort.Slice(records, func(left, right int) bool { return records[left].key < records[right].key })
	return records, nil
}

func gitOutput(repo string, arguments ...string) (string, error) {
	command := exec.Command("git", append([]string{"-C", repo}, arguments...)...)
	output, err := command.Output()
	if err != nil {
		var exitError *exec.ExitError
		if errors.As(err, &exitError) {
			return "", fmt.Errorf("git %s: %s", strings.Join(arguments, " "), strings.TrimSpace(string(exitError.Stderr)))
		}
		return "", err
	}
	return string(output), nil
}

func resolveCommit(repo, ref string) (string, error) {
	trimmed := strings.TrimSpace(ref)
	if len(trimmed) != 40 {
		return "", fmt.Errorf("ref %q is not an immutable 40-character commit SHA", ref)
	}
	for _, character := range trimmed {
		if (character < '0' || character > '9') && (character < 'a' || character > 'f') && (character < 'A' || character > 'F') {
			return "", fmt.Errorf("ref %q is not an immutable commit SHA", ref)
		}
	}
	resolved, err := gitOutput(repo, "rev-parse", "--verify", trimmed+"^{commit}")
	if err != nil {
		return "", err
	}
	resolved = strings.TrimSpace(resolved)
	if !strings.EqualFold(resolved, trimmed) {
		return "", fmt.Errorf("ref %q resolved to unexpected commit %q", ref, resolved)
	}
	return resolved, nil
}
