package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

const (
	defaultNewAPIRef  = "bdef1175" + "05247769" + "268b2096" + "65fb3ad7" + "554c3da7"
	defaultBifrostRef = "c5c02ae7" + "47fe294a" + "7f7f7652" + "77aae08b" + "bf0835fa"
)

type sourceRecord struct {
	// BaseModel is source metadata only; source keys, never this hint, define
	// catalog identity.
	BaseModel                   string            `json:"base_model"`
	Provider                    string            `json:"provider"`
	Source                      string            `json:"source"`
	Mode                        string            `json:"mode"`
	MaxInputTokens              *json.Number      `json:"max_input_tokens"`
	MaxOutputTokens             *json.Number      `json:"max_output_tokens"`
	MaxTokens                   *json.Number      `json:"max_tokens"`
	OutputVectorSize            *json.Number      `json:"output_vector_size"`
	ModelParameters             []sourceParameter `json:"model_parameters"`
	SupportedEndpoints          []string          `json:"supported_endpoints"`
	SupportedModalities         []string          `json:"supported_modalities"`
	SupportedOutputModalities   []string          `json:"supported_output_modalities"`
	SupportsAudioInput          bool              `json:"supports_audio_input"`
	SupportsAudioOutput         bool              `json:"supports_audio_output"`
	SupportsEmbeddingImageInput bool              `json:"supports_embedding_image_input"`
	SupportsFunctionCalling     bool              `json:"supports_function_calling"`
	SupportsImageInput          bool              `json:"supports_image_input"`
	SupportsNativeStreaming     bool              `json:"supports_native_streaming"`
	// These source flags are compatibility evidence for the model/service
	// combination. The catalog keeps the model-side hint, while admission still
	// requires protocol and service support before promising strict output.
	SupportsNativeStructured bool  `json:"supports_native_structured_output"`
	SupportsPromptCaching    bool  `json:"supports_prompt_caching"`
	SupportsCachePoint       bool  `json:"supports_cache_point"`
	SupportsResponseSchema   bool  `json:"supports_response_schema"`
	SupportsSamplingParams   *bool `json:"supports_sampling_params"`
	SupportsVideoInput       bool  `json:"supports_video_input"`
	SupportsVision           bool  `json:"supports_vision"`
	IsDeprecated             bool  `json:"is_deprecated"`
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

func extractNewAPIModels(repo, ref string) (map[string]struct{}, error) {
	listing, err := gitOutput(repo, "ls-tree", "-r", "--name-only", ref)
	if err != nil {
		return nil, fmt.Errorf("list new-api source tree: %w", err)
	}
	models := make(map[string]struct{})
	for _, name := range strings.Fields(listing) {
		if !strings.HasPrefix(name, "relay/channel/") || !strings.HasSuffix(name, ".go") {
			continue
		}
		content, showErr := gitOutput(repo, "show", ref+":"+name)
		if showErr != nil {
			return nil, fmt.Errorf("read new-api %s: %w", name, showErr)
		}
		file, parseErr := parser.ParseFile(token.NewFileSet(), name, content, 0)
		if parseErr != nil {
			return nil, fmt.Errorf("parse new-api %s: %w", name, parseErr)
		}
		parts := strings.Split(filepath.ToSlash(name), "/")
		if len(parts) < 3 {
			continue
		}
		channel := parts[2]
		stringConstants := collectStringConstants(file)
		ast.Inspect(file, func(node ast.Node) bool {
			spec, ok := node.(*ast.ValueSpec)
			if !ok || len(spec.Names) != 1 || spec.Names[0].Name != "ModelList" || len(spec.Values) != 1 {
				return true
			}
			literal, ok := spec.Values[0].(*ast.CompositeLit)
			if !ok {
				return false
			}
			for _, element := range literal.Elts {
				modelID, ok := resolveStringLiteral(element, stringConstants)
				if !ok || modelID == "" || syntheticNewAPIModel(channel, modelID) {
					continue
				}
				models[modelID] = struct{}{}
			}
			return false
		})
	}
	return models, nil
}

func collectStringConstants(file *ast.File) map[string]string {
	constants := make(map[string]string)
	for _, declaration := range file.Decls {
		group, ok := declaration.(*ast.GenDecl)
		if !ok || group.Tok != token.CONST {
			continue
		}
		for _, specification := range group.Specs {
			values, ok := specification.(*ast.ValueSpec)
			if !ok || len(values.Names) != len(values.Values) {
				continue
			}
			for index, name := range values.Names {
				value, ok := resolveStringLiteral(values.Values[index], constants)
				if ok {
					constants[name.Name] = value
				}
			}
		}
	}
	return constants
}

func resolveStringLiteral(expression ast.Expr, constants map[string]string) (string, bool) {
	switch value := expression.(type) {
	case *ast.BasicLit:
		if value.Kind != token.STRING {
			return "", false
		}
		literal, err := strconv.Unquote(value.Value)
		return literal, err == nil
	case *ast.Ident:
		literal, ok := constants[value.Name]
		return literal, ok
	default:
		return "", false
	}
}

func syntheticNewAPIModel(channel, modelID string) bool {
	if syntheticModelID(modelID) {
		return true
	}
	suffix := func(values ...string) bool {
		for _, value := range values {
			if strings.HasSuffix(modelID, "-"+value) {
				return true
			}
		}
		return false
	}
	switch channel {
	case "codex":
		return modelID == "codex-auto-review"
	case "claude":
		return suffix("thinking", "max", "xhigh", "high", "medium", "low")
	case "deepseek":
		return suffix("none", "max")
	case "openai":
		base := strings.TrimSuffix(modelID, "-high")
		base = strings.TrimSuffix(base, "-medium")
		base = strings.TrimSuffix(base, "-low")
		return base != modelID && (base == "o3-mini" || base == "o3-mini-2025-01-31")
	case "xai":
		return strings.HasSuffix(modelID, "-search") || modelID == "grok-3-mini-high" || modelID == "grok-3-mini-low"
	default:
		return false
	}
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
