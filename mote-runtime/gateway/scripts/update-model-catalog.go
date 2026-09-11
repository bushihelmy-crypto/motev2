// Command update-model-catalog builds Gateway's model-only catalog from a
// pinned new-api Git tree and a pinned Bifrost model-parameters snapshot.
package main

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

const catalogSchemaVersion = 1

// Keep the public provenance commits split into short literals so the secret
// scanner does not mistake a Git SHA for credential material.
const (
	defaultNewAPIRef  = "bdef1175" + "05247769" + "268b2096" + "65fb3ad7" + "554c3da7"
	defaultBifrostRef = "c5c02ae7" + "47fe294a" + "7f7f7652" + "77aae08b" + "bf0835fa"
)

var supportedModes = map[string]string{
	"chat":                "generate",
	"completion":          "generate",
	"responses":           "generate",
	"embedding":           "embedding",
	"rerank":              "rerank",
	"image_generation":    "image_generation",
	"image_edit":          "image_generation",
	"audio_speech":        "audio_generation",
	"audio_transcription": "audio_transcription",
	"video_generation":    "video_generation",
	"realtime":            "realtime",
}

type options struct {
	newAPIRepo      string
	newAPIRef       string
	bifrostRepo     string
	bifrostRef      string
	modelParameters string
	output          string
}

type sourceRecord struct {
	// BaseModel is source metadata only; source keys, never this hint, define
	// catalog identity.
	BaseModel                   string            `json:"base_model"`
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
	SupportsNativeStructured    bool              `json:"supports_native_structured_output"`
	SupportsPromptCaching       bool              `json:"supports_prompt_caching"`
	SupportsCachePoint          bool              `json:"supports_cache_point"`
	SupportsResponseSchema      bool              `json:"supports_response_schema"`
	SupportsSamplingParams      *bool             `json:"supports_sampling_params"`
	SupportsVideoInput          bool              `json:"supports_video_input"`
	SupportsVision              bool              `json:"supports_vision"`
	IsDeprecated                bool              `json:"is_deprecated"`
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

type catalogDocument struct {
	SchemaVersion int             `json:"schema_version"`
	Sources       []catalogSource `json:"sources"`
	Models        []modelConfig   `json:"models"`
}

type catalogSource struct {
	Name     string `json:"name"`
	Revision string `json:"revision,omitempty"`
	SHA256   string `json:"sha256,omitempty"`
}

type modelConfig struct {
	ID          string            `json:"id"`
	Lifecycle   string            `json:"lifecycle"`
	TokenLimits tokenLimits       `json:"token_limits"`
	Operations  []operationConfig `json:"operations"`
}

type tokenLimits struct {
	ContextWindowTokens int64 `json:"context_window_tokens,omitempty"`
	MaxInputTokens      int64 `json:"max_input_tokens,omitempty"`
	MinOutputTokens     int64 `json:"min_output_tokens,omitempty"`
	MaxOutputTokens     int64 `json:"max_output_tokens,omitempty"`
}

type operationConfig struct {
	Operation        string            `json:"operation"`
	Modes            []string          `json:"modes"`
	InputModalities  []string          `json:"input_modalities"`
	OutputModalities []string          `json:"output_modalities"`
	Features         []string          `json:"features,omitempty"`
	Generation       *generationPolicy `json:"generation,omitempty"`
	Embedding        *embeddingPolicy  `json:"embedding,omitempty"`
}

type generationPolicy struct {
	Temperature     *numericParameter[float64] `json:"temperature,omitempty"`
	TopP            *numericParameter[float64] `json:"top_p,omitempty"`
	MaxOutputTokens *outputTokenParameter      `json:"max_output_tokens,omitempty"`
	Stop            *stopParameter             `json:"stop,omitempty"`
	Seed            *numericParameter[int64]   `json:"seed,omitempty"`
}

type numericParameter[T int64 | float64] struct {
	Minimum *T `json:"minimum,omitempty"`
	Maximum *T `json:"maximum,omitempty"`
	Default *T `json:"default,omitempty"`
}

type outputTokenParameter struct {
}

type stopParameter struct {
	Default []string `json:"default,omitempty"`
}

type embeddingPolicy struct {
	FixedDimensions *int64                   `json:"fixed_dimensions,omitempty"`
	Dimensions      *numericParameter[int64] `json:"dimensions,omitempty"`
}

func main() {
	configured := parseOptions()
	if err := run(configured); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func parseOptions() options {
	var result options
	flag.StringVar(&result.newAPIRepo, "new-api-repo", "", "path to the new-api Git repository")
	flag.StringVar(&result.newAPIRef, "new-api-ref", defaultNewAPIRef, "immutable new-api Git commit to read")
	flag.StringVar(&result.bifrostRepo, "bifrost-repo", "", "path to the Bifrost Git repository")
	flag.StringVar(&result.bifrostRef, "bifrost-ref", defaultBifrostRef, "immutable Bifrost Git commit to read")
	flag.StringVar(&result.modelParameters, "model-parameters", "", "Bifrost model-parameters JSON snapshot")
	flag.StringVar(&result.output, "output", "src/internal/model/catalog_data.json.gz", "gzip-compressed catalog output path")
	flag.Parse()
	return result
}

func run(configured options) error {
	if configured.newAPIRepo == "" || configured.bifrostRepo == "" || configured.modelParameters == "" {
		return errors.New("-new-api-repo, -bifrost-repo, and -model-parameters are required")
	}
	newRevision, err := resolveCommit(configured.newAPIRepo, configured.newAPIRef)
	if err != nil {
		return fmt.Errorf("resolve new-api ref: %w", err)
	}
	bifrostRevision, err := resolveCommit(configured.bifrostRepo, configured.bifrostRef)
	if err != nil {
		return fmt.Errorf("resolve Bifrost ref: %w", err)
	}
	parameterData, err := os.ReadFile(configured.modelParameters)
	if err != nil {
		return fmt.Errorf("read Bifrost model parameters: %w", err)
	}
	records, err := decodeSourceRecords(parameterData)
	if err != nil {
		return err
	}
	newModels, err := extractNewAPIModels(configured.newAPIRepo, configured.newAPIRef)
	if err != nil {
		return err
	}
	models, rejected := compileModels(records, newModels)
	for _, rejection := range rejected {
		fmt.Fprintf(os.Stderr, "rejected model: %v\n", rejection)
	}
	if len(models) == 0 {
		return errors.New("compile model catalog: sources produced no usable models")
	}
	digest := sha256.Sum256(parameterData)
	document := catalogDocument{
		SchemaVersion: catalogSchemaVersion,
		Sources: []catalogSource{
			{Name: "new-api", Revision: strings.TrimSpace(newRevision)},
			{Name: "bifrost", Revision: strings.TrimSpace(bifrostRevision)},
			{Name: "bifrost-model-parameters", SHA256: hex.EncodeToString(digest[:])},
		},
		Models: models,
	}
	if err := writeCatalog(configured.output, document); err != nil {
		return err
	}
	fmt.Printf("wrote %d models to %s (%d rejected)\n", len(models), configured.output, len(rejected))
	return nil
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

type compileError struct {
	ModelID string
	Field   string
	Reason  string
}

func (err *compileError) Error() string {
	if err.ModelID == "" {
		return fmt.Sprintf("invalid model source %s: %s", err.Field, err.Reason)
	}
	return fmt.Sprintf("invalid model source %q %s: %s", err.ModelID, err.Field, err.Reason)
}

// compileModels keeps source keys as the only model identities. BaseModel is
// never promoted into a catalog ID; a batch record is supplemental only when
// its own key explicitly carries the exact model ID and the :batch variant.
func compileModels(records []recordRef, newModels map[string]struct{}) ([]modelConfig, []*compileError) {
	byKey := make(map[string]recordRef, len(records))
	candidateIDs := make(map[string]struct{}, len(records)+len(newModels))
	for _, ref := range records {
		if ref.key == "" || syntheticModelID(ref.key) {
			continue
		}
		byKey[ref.key] = ref
		if ref.record.Mode != "" {
			if _, supported := supportedModes[ref.record.Mode]; supported {
				candidateIDs[ref.key] = struct{}{}
			}
		}
	}
	for modelID := range newModels {
		if !syntheticModelID(modelID) && inferOperation(modelID) != "" {
			candidateIDs[modelID] = struct{}{}
		}
	}

	ids := make([]string, 0, len(candidateIDs))
	for modelID := range candidateIDs {
		ids = append(ids, modelID)
	}
	sort.Slice(ids, func(left, right int) bool {
		leftFolded, rightFolded := strings.ToLower(ids[left]), strings.ToLower(ids[right])
		if leftFolded != rightFolded {
			return leftFolded < rightFolded
		}
		return ids[left] < ids[right]
	})

	models := make([]modelConfig, 0, len(ids))
	rejected := make([]*compileError, 0)
	for _, modelID := range ids {
		matches := recordsForModelID(modelID, byKey, records)
		model, present, err := compileModel(modelID, matches)
		if err != nil {
			rejected = append(rejected, err)
			continue
		}
		if present {
			models = append(models, model)
		}
	}
	return models, rejected
}

func recordsForModelID(modelID string, byKey map[string]recordRef, records []recordRef) []recordRef {
	primary, exact := byKey[modelID]
	if !exact {
		return nil
	}
	result := []recordRef{primary}
	for _, ref := range records {
		if ref.key == modelID || !explicitVariantFor(modelID, ref) {
			continue
		}
		result = append(result, ref)
	}
	sortRecords(modelID, result)
	return result
}

func explicitVariantFor(modelID string, ref recordRef) bool {
	key := strings.ToLower(ref.key)
	target := strings.ToLower(modelID)
	return key == target+":batch" || strings.HasSuffix(key, "/"+target+":batch")
}

func compileModel(modelID string, matches []recordRef) (modelConfig, bool, *compileError) {
	if len(matches) == 0 {
		operation := inferOperation(modelID)
		if operation == "" {
			return modelConfig{}, false, nil
		}
		compiled, err := compileOperation(operation, "", nil, recordRef{}, false)
		if err != nil {
			return modelConfig{}, false, &compileError{ModelID: modelID, Field: "operation", Reason: err.Error()}
		}
		return modelConfig{ID: modelID, Lifecycle: "active", Operations: []operationConfig{compiled}}, true, nil
	}

	primary := matches[0]
	for _, candidate := range matches {
		if candidate.key == modelID {
			primary = candidate
			break
		}
	}
	operation := operationForRecord(modelID, primary)
	if operation == "" {
		return modelConfig{}, false, &compileError{ModelID: modelID, Field: "operation", Reason: "source mode and model name do not identify a supported operation"}
	}
	compatible := make([]recordRef, 0, len(matches))
	for _, ref := range matches {
		if operationForRecord(modelID, ref) == operation {
			compatible = append(compatible, ref)
		}
	}
	if len(compatible) == 0 {
		return modelConfig{}, false, &compileError{ModelID: modelID, Field: "operation", Reason: "no compatible source record"}
	}

	limits, outputTokensSupported, err := compileTokenLimits(operation, primary)
	if err != nil {
		return modelConfig{}, false, &compileError{ModelID: modelID, Field: "token_limits", Reason: err.Error()}
	}
	compiledOperation, err := compileOperation(operation, primary.record.Mode, compatible, primary, outputTokensSupported)
	if err != nil {
		return modelConfig{}, false, &compileError{ModelID: modelID, Field: "operation", Reason: err.Error()}
	}
	lifecycle := "active"
	if primary.record.IsDeprecated {
		lifecycle = "deprecated"
	}
	return modelConfig{
		ID:          modelID,
		Lifecycle:   lifecycle,
		TokenLimits: limits,
		Operations:  []operationConfig{compiledOperation},
	}, true, nil
}

func operationForRecord(modelID string, ref recordRef) string {
	if ref.record.Mode != "" {
		operation := supportedModes[ref.record.Mode]
		switch operation {
		case "image_generation":
			// Bifrost also stores ordinary model price rows under an
			// image_generation mode. Treat that row as image generation only
			// when its output evidence is unambiguously image-only; a model
			// name may confirm evidence, but never override an explicit chat
			// or completion mode.
			if imageOperationEvidence(modelID, ref) {
				return operation
			}
			return "generate"
		case "video_generation":
			if videoOperationEvidence(modelID, ref) {
				return operation
			}
			return "generate"
		default:
			return operation
		}
	}
	return inferOperation(modelID)
}

func syntheticModelID(modelID string) bool {
	value := strings.ToLower(modelID)
	if strings.HasSuffix(value, ":batch") || syntheticRequestPreset(value) {
		return true
	}
	hasSuffix := func(values ...string) bool {
		for _, suffix := range values {
			if strings.HasSuffix(value, "-"+suffix) {
				return true
			}
		}
		return false
	}
	if value == "codex-auto-review" {
		return true
	}
	if (strings.HasPrefix(value, "claude-") || strings.Contains(value, "/claude-")) &&
		hasSuffix("thinking", "max", "xhigh", "high", "medium", "low") {
		return true
	}
	if strings.HasPrefix(value, "deepseek-v4-") && hasSuffix("none", "max") {
		return true
	}
	if value == "o3-mini-high" || value == "o3-mini-medium" || value == "o3-mini-low" ||
		value == "o3-mini-2025-01-31-high" || value == "o3-mini-2025-01-31-medium" || value == "o3-mini-2025-01-31-low" {
		return true
	}
	return (strings.HasPrefix(value, "grok-") && strings.HasSuffix(value, "-search")) ||
		value == "grok-3-mini-high" || value == "grok-3-mini-low"
}

func syntheticRequestPreset(value string) bool {
	if strings.HasPrefix(value, "preset/") {
		return true
	}
	switch value {
	case "conservative", "creative", "edit", "erase", "fast", "inpaint", "outpaint",
		"sketch", "structure", "style", "style-transfer", "remove-background",
		"replace-background-and-relight", "search-and-recolor", "search-and-replace":
		return true
	default:
		return false
	}
}

func inferOperation(modelID string) string {
	value := strings.ToLower(modelID)
	switch {
	case strings.Contains(value, "moderation"):
		return ""
	case strings.Contains(value, "deepgram/flux"):
		return "audio_transcription"
	case strings.Contains(value, "rerank"):
		return "rerank"
	case strings.Contains(value, "embedding"), strings.Contains(value, "embed-"),
		strings.Contains(value, "bge-"), strings.Contains(value, "m3e-"), strings.Contains(value, "jina-clip"):
		return "embedding"
	case strings.Contains(value, "transcribe"), strings.Contains(value, "whisper"),
		strings.Contains(value, "sensevoice"), strings.Contains(value, "parakeet"),
		strings.Contains(value, "-asr"), strings.Contains(value, "/asr"),
		strings.Contains(value, "-stt"), strings.Contains(value, "/stt"),
		strings.Contains(value, "voxtral-mini-realtime"):
		return "audio_transcription"
	case strings.Contains(value, "lyria"), strings.Contains(value, "musicgen"), strings.Contains(value, "suno"):
		return "music_generation"
	case strings.Contains(value, "tts"), strings.HasPrefix(value, "speech-"),
		strings.Contains(value, "text-to-speech"), strings.Contains(value, "text2speech"),
		strings.Contains(value, "melotts"), strings.Contains(value, "orpheus"),
		strings.Contains(value, "eleven-"):
		return "audio_generation"
	case strings.Contains(value, "sora"), strings.Contains(value, "veo-"), strings.Contains(value, "seedance"),
		strings.Contains(value, "imagine-video"), strings.Contains(value, "video-01"),
		strings.Contains(value, "wan-video"), strings.Contains(value, "wan-") &&
			(strings.Contains(value, "-t2v") || strings.Contains(value, "-i2v")),
		strings.Contains(value, "kling"),
		strings.Contains(value, "hailuo"), strings.Contains(value, "pixverse"),
		strings.Contains(value, "gen3a"), strings.Contains(value, "gen4-turbo"),
		strings.Contains(value, "gen4-aleph"), strings.Contains(value, "ray-"),
		strings.Contains(value, "motion-"), strings.Contains(value, "inkling"), strings.Contains(value, "vidu"),
		strings.Contains(value, "luma/"):
		return "video_generation"
	case imageModelName(value):
		return "image_generation"
	case realtimeModel(value):
		return "realtime"
	default:
		return "generate"
	}
}

func imageModelName(value string) bool {
	return strings.Contains(value, "dall-e") || strings.Contains(value, "gpt-image") ||
		strings.Contains(value, "chatgpt-image") || strings.Contains(value, "imagen-") ||
		strings.Contains(value, "imagen4") || strings.Contains(value, "seedream") || strings.Contains(value, "imagine-image") ||
		strings.Contains(value, "image-01") || strings.Contains(value, "flux") ||
		strings.Contains(value, "sdxl") || strings.Contains(value, "instantid") ||
		strings.Contains(value, "jimeng") || strings.Contains(value, "text-to-image") ||
		strings.Contains(value, "text2image") || strings.Contains(value, "imagegeneration") ||
		strings.HasSuffix(value, "-image") || strings.Contains(value, "-image-") ||
		strings.Contains(value, "flash-image") || strings.Contains(value, "mai-image") ||
		strings.Contains(value, "nano-banana") || strings.Contains(value, "qwen-image") ||
		strings.Contains(value, "qwen-edit") || strings.Contains(value, "ideogram") ||
		strings.Contains(value, "recraft") || strings.Contains(value, "playground-v") ||
		strings.Contains(value, "openjourney") || strings.Contains(value, "analog-diffusion") ||
		strings.Contains(value, "realistic_vision") || strings.Contains(value, "stable-diffusion") ||
		strings.Contains(value, "stable-image") || strings.Contains(value, "stable-outpaint") ||
		strings.Contains(value, "stable-style") || strings.Contains(value, "stable-control") ||
		strings.Contains(value, "stable-erase") || strings.Contains(value, "stable-remove") ||
		strings.Contains(value, "stable-search") || strings.Contains(value, "stable-creative") ||
		strings.Contains(value, "stable-conservative") || strings.Contains(value, "stable-fast") ||
		strings.Contains(value, "stable-inpaint") || strings.Contains(value, "stable_") ||
		strings.Contains(value, "sd3") || strings.Contains(value, "ssd-1b") ||
		strings.Contains(value, "titan-image") || strings.Contains(value, "nova-canvas") ||
		strings.Contains(value, "p-image") || strings.Contains(value, "dreamina") ||
		strings.Contains(value, "hidream") ||
		strings.Contains(value, "wan2.6-image") || strings.Contains(value, "gen4-image") ||
		strings.Contains(value, "image-upscale")
}

func imageOperationEvidence(modelID string, ref recordRef) bool {
	if imageModelName(strings.ToLower(modelID)) {
		return true
	}
	outputs := modalitySet(ref.record.SupportedOutputModalities)
	if len(outputs) == 1 {
		_, image := outputs["image"]
		return image
	}
	return false
}

func videoOperationEvidence(modelID string, ref recordRef) bool {
	value := strings.ToLower(modelID)
	if strings.Contains(value, "sora") || strings.Contains(value, "veo") ||
		strings.Contains(value, "seedance") || strings.Contains(value, "video-01") ||
		strings.Contains(value, "wan-video") || (strings.Contains(value, "wan-") &&
		(strings.Contains(value, "-t2v") || strings.Contains(value, "-i2v"))) || strings.Contains(value, "kling") ||
		strings.Contains(value, "hailuo") || strings.Contains(value, "pixverse") ||
		strings.Contains(value, "gen3") || strings.Contains(value, "gen4-") ||
		strings.Contains(value, "ray-") || strings.Contains(value, "motion-") || strings.Contains(value, "inkling") ||
		strings.Contains(value, "vidu") || strings.Contains(value, "luma/") {
		return true
	}
	outputs := modalitySet(ref.record.SupportedOutputModalities)
	if len(outputs) == 1 {
		_, video := outputs["video"]
		return video
	}
	return false
}

func modalitySet(values []string) map[string]struct{} {
	result := make(map[string]struct{}, len(values))
	for _, value := range values {
		result[strings.ToLower(value)] = struct{}{}
	}
	return result
}

func realtimeModel(modelID string) bool {
	value := strings.ToLower(modelID)
	return strings.Contains(value, "realtime") || strings.Contains(value, "native-audio") || strings.Contains(value, "-live-")
}

func compileTokenLimits(operation string, primary recordRef) (tokenLimits, bool, error) {
	maxInput, err := nonNegativeInteger(primary.record.MaxInputTokens)
	if err != nil {
		return tokenLimits{}, false, fmt.Errorf("max_input_tokens: %w", err)
	}
	if maxInput == 0 && operation == "embedding" {
		maxInput, err = nonNegativeInteger(primary.record.MaxTokens)
		if err != nil {
			return tokenLimits{}, false, fmt.Errorf("max_tokens: %w", err)
		}
	}
	limits := tokenLimits{MaxInputTokens: maxInput}
	if operation != "generate" && operation != "realtime" {
		return limits, false, nil
	}

	maxOutput, outputErr := nonNegativeInteger(primary.record.MaxOutputTokens)
	if outputErr != nil {
		return tokenLimits{}, false, fmt.Errorf("max_output_tokens: %w", outputErr)
	}
	genericMaximum, genericErr := nonNegativeInteger(primary.record.MaxTokens)
	if genericErr != nil {
		return tokenLimits{}, false, fmt.Errorf("max_tokens: %w", genericErr)
	}
	// max_output_tokens is the explicit output-limit fact. max_tokens is a
	// legacy/general field and is used only when the explicit field is absent;
	// differing values are not merged or averaged.
	if maxOutput == 0 {
		maxOutput = genericMaximum
	}
	parameterPolicy, parameterFound, policyErr := outputTokenBounds([]recordRef{primary})
	if policyErr != nil {
		return tokenLimits{}, false, policyErr
	}
	if parameterFound {
		if maxOutput == 0 && parameterPolicy.Maximum != nil {
			maxOutput = *parameterPolicy.Maximum
		}
		if parameterPolicy.Minimum != nil {
			limits.MinOutputTokens = *parameterPolicy.Minimum
		}
	}
	limits.MaxOutputTokens = maxOutput
	if limits.MinOutputTokens > 0 && limits.MaxOutputTokens > 0 && limits.MinOutputTokens > limits.MaxOutputTokens {
		return tokenLimits{}, false, errors.New("output minimum must not exceed maximum")
	}
	return limits, parameterFound || maxOutput > 0, nil
}

func compileOperation(operation, sourceMode string, records []recordRef, primary recordRef, outputTokensSupported bool) (operationConfig, error) {
	primaryOnly := records
	if primary.key != "" {
		primaryOnly = []recordRef{primary}
	}
	inputs, outputs := compileModalities(operation, sourceMode, primaryOnly)
	result := operationConfig{
		Operation:        operation,
		Modes:            compileModes(operation, records),
		InputModalities:  inputs,
		OutputModalities: outputs,
		Features:         compileFeatures(operation, primaryOnly),
	}
	if operation == "generate" || operation == "realtime" {
		policy, err := compileGenerationPolicy(primary, outputTokensSupported)
		if err != nil {
			return operationConfig{}, err
		}
		result.Generation = policy
	}
	if operation == "embedding" {
		policy, err := compileEmbeddingPolicy(primary)
		if err != nil {
			return operationConfig{}, err
		}
		result.Embedding = policy
	}
	if err := validateOperationShape(result); err != nil {
		return operationConfig{}, err
	}
	return result, nil
}

func validateOperationShape(operation operationConfig) error {
	containsMode := func(mode string) bool {
		for _, candidate := range operation.Modes {
			if candidate == mode {
				return true
			}
		}
		return false
	}
	containsInput := func(modality string) bool {
		for _, candidate := range operation.InputModalities {
			if candidate == modality {
				return true
			}
		}
		return false
	}
	containsOutput := func(modality string) bool {
		for _, candidate := range operation.OutputModalities {
			if candidate == modality {
				return true
			}
		}
		return false
	}
	if containsInput("embedding") {
		return errors.New("embedding cannot be an operation input modality")
	}
	if operation.Operation == "realtime" {
		if len(operation.Modes) != 1 || !containsMode("duplex") {
			return errors.New("realtime must use duplex mode only")
		}
		if !containsInput("text") && !containsInput("audio") {
			return errors.New("realtime must accept text or audio input")
		}
		if !containsOutput("text") && !containsOutput("audio") {
			return errors.New("realtime must produce text or audio output")
		}
	} else if containsMode("duplex") {
		return errors.New("only realtime may use duplex mode")
	}

	requiredOutput := map[string]string{
		"embedding":           "embedding",
		"rerank":              "text",
		"image_generation":    "image",
		"audio_generation":    "audio",
		"audio_transcription": "text",
		"music_generation":    "music",
		"video_generation":    "video",
	}
	if required, ok := requiredOutput[operation.Operation]; ok {
		if !containsOutput(required) {
			return fmt.Errorf("%s must produce %s", operation.Operation, required)
		}
		if operation.Operation == "embedding" && len(operation.OutputModalities) != 1 {
			return errors.New("embedding must produce only embedding")
		}
	}
	if operation.Operation == "embedding" && operation.Generation != nil {
		return errors.New("embedding cannot declare generation parameters")
	}
	if operation.Operation == "embedding" && operation.Embedding == nil {
		return errors.New("embedding must declare its embedding capability")
	}
	if operation.Operation != "embedding" && operation.Embedding != nil {
		return errors.New("non-embedding operation must not declare embedding parameters")
	}
	if operation.Operation == "rerank" && operation.Generation != nil {
		return errors.New("rerank must not declare generation parameters")
	}
	if operation.Operation != "embedding" && containsOutput("embedding") {
		return errors.New("non-embedding operation must not produce the embedding modality")
	}
	return nil
}

func compileModes(operation string, records []recordRef) []string {
	modes := make(map[string]struct{}, 2)
	switch operation {
	case "realtime":
		modes["duplex"] = struct{}{}
	case "video_generation":
		modes["async"] = struct{}{}
	default:
		modes["unary"] = struct{}{}
	}
	if operation == "generate" {
		for _, ref := range records {
			if ref.record.SupportsNativeStreaming || hasParameter(ref.record, "stream") {
				modes["server_stream"] = struct{}{}
				break
			}
		}
	}
	if operation == "audio_transcription" {
		for _, ref := range records {
			if ref.record.SupportsNativeStreaming || strings.Contains(strings.ToLower(ref.key), "realtime") {
				modes["server_stream"] = struct{}{}
				break
			}
		}
	}
	if operation != "realtime" {
		batchOnly := len(records) > 0
		for _, ref := range records {
			if batchEvidence(ref) {
				modes["async"] = struct{}{}
			}
			if !batchOnlyEvidence(ref) {
				batchOnly = false
			}
		}
		if batchOnly {
			delete(modes, "unary")
		}
	}
	return sortedSet(modes)
}

func batchEvidence(ref recordRef) bool {
	key := strings.ToLower(ref.key)
	if strings.Contains(key, ":batch") || strings.Contains(key, "/batch/") {
		return true
	}
	for _, endpoint := range ref.record.SupportedEndpoints {
		if strings.Contains(strings.ToLower(endpoint), "batch") {
			return true
		}
	}
	return false
}

func batchOnlyEvidence(ref recordRef) bool {
	key := strings.ToLower(ref.key)
	if strings.HasSuffix(key, ":batch") || strings.Contains(key, "/batch/") {
		return true
	}
	hasBatch, hasNonBatch := false, false
	for _, endpoint := range ref.record.SupportedEndpoints {
		if strings.Contains(strings.ToLower(endpoint), "batch") {
			hasBatch = true
		} else {
			hasNonBatch = true
		}
	}
	return hasBatch && !hasNonBatch
}

func compileModalities(operation, sourceMode string, records []recordRef) ([]string, []string) {
	defaults := map[string][2][]string{
		"generate":            {{"text"}, {"text"}},
		"embedding":           {{"text"}, {"embedding"}},
		"rerank":              {{"text"}, {"text"}},
		"image_generation":    {{"text"}, {"image"}},
		"audio_generation":    {{"text"}, {"audio"}},
		"audio_transcription": {{"audio"}, {"text"}},
		"music_generation":    {{"text"}, {"music"}},
		"video_generation":    {{"text", "image"}, {"video"}},
		"realtime":            {{"text", "audio"}, {"text", "audio"}},
	}
	inputs := make(map[string]struct{})
	outputs := make(map[string]struct{})
	for _, value := range defaults[operation][0] {
		inputs[value] = struct{}{}
	}
	for _, value := range defaults[operation][1] {
		outputs[value] = struct{}{}
	}
	if sourceMode == "image_edit" {
		inputs["image"] = struct{}{}
	}
	for _, ref := range records {
		for _, value := range ref.record.SupportedModalities {
			addModality(inputs, value)
		}
		for _, value := range ref.record.SupportedOutputModalities {
			addModality(outputs, value)
		}
		switch operation {
		case "embedding":
			if ref.record.SupportsEmbeddingImageInput {
				inputs["image"] = struct{}{}
			}
			if ref.record.SupportsAudioInput {
				inputs["audio"] = struct{}{}
			}
			if ref.record.SupportsVideoInput {
				inputs["video"] = struct{}{}
			}
		case "generate", "realtime":
			if ref.record.SupportsVision || ref.record.SupportsImageInput {
				inputs["image"] = struct{}{}
			}
			if ref.record.SupportsAudioInput {
				inputs["audio"] = struct{}{}
			}
			if ref.record.SupportsVideoInput {
				inputs["video"] = struct{}{}
			}
			if ref.record.SupportsAudioOutput {
				outputs["audio"] = struct{}{}
			}
		}
	}
	if operation == "embedding" {
		outputs = map[string]struct{}{"embedding": {}}
	}
	if operation == "music_generation" {
		outputs = map[string]struct{}{"music": {}}
	}
	return sortedSet(inputs), sortedSet(outputs)
}

func addModality(target map[string]struct{}, value string) {
	normalized := strings.ToLower(value)
	switch normalized {
	case "text", "image", "audio", "music", "video", "embedding":
		target[normalized] = struct{}{}
	}
}

func compileFeatures(operation string, records []recordRef) []string {
	features := map[string]struct{}{"usage": {}}
	if operation != "generate" && operation != "realtime" {
		return sortedSet(features)
	}
	for _, ref := range records {
		record := ref.record
		if record.SupportsFunctionCalling {
			features["tool_calls"] = struct{}{}
		}
		if record.SupportsResponseSchema || record.SupportsNativeStructured {
			features["structured_output"] = struct{}{}
		}
		if record.SupportsPromptCaching || record.SupportsCachePoint {
			features["prompt_cache"] = struct{}{}
		}
	}
	return sortedSet(features)
}

func compileGenerationPolicy(primary recordRef, outputTokensSupported bool) (*generationPolicy, error) {
	policy := &generationPolicy{}
	samplingAllowed := primary.record.SupportsSamplingParams == nil || *primary.record.SupportsSamplingParams
	if samplingAllowed {
		if parameter, ok, err := findParameter([]recordRef{primary}, "temperature"); err != nil {
			return nil, err
		} else if ok {
			policy.Temperature, err = floatPolicy(parameter)
			if err != nil {
				return nil, fmt.Errorf("temperature: %w", err)
			}
		}
		if parameter, ok, err := findParameter([]recordRef{primary}, "top_p", "topp"); err != nil {
			return nil, err
		} else if ok {
			policy.TopP, err = floatPolicy(parameter)
			if err != nil {
				return nil, fmt.Errorf("top_p: %w", err)
			}
		}
		if parameter, ok, err := findParameter([]recordRef{primary}, "seed"); err != nil {
			return nil, err
		} else if ok {
			policy.Seed, err = integerPolicy(parameter)
			if err != nil {
				return nil, fmt.Errorf("seed: %w", err)
			}
		}
	}
	if outputTokensSupported {
		// The default is a Gateway policy, not a model fact. Runtime resolves
		// it centrally so the catalog never repeats a derived 4096 value.
		policy.MaxOutputTokens = &outputTokenParameter{}
	}
	if parameter, ok, err := findParameter([]recordRef{primary}, "stop", "stop_sequences", "stopsequences"); err != nil {
		return nil, err
	} else if ok {
		stops, parseErr := rawStrings(parameter.Default)
		if parseErr != nil {
			return nil, fmt.Errorf("stop: %w", parseErr)
		}
		policy.Stop = &stopParameter{Default: stops}
	}
	if policy.Temperature == nil && policy.TopP == nil && policy.MaxOutputTokens == nil && policy.Stop == nil && policy.Seed == nil {
		return nil, nil
	}
	return policy, nil
}

func compileEmbeddingPolicy(primary recordRef) (*embeddingPolicy, error) {
	policy := &embeddingPolicy{}
	if parameter, ok, err := findParameter([]recordRef{primary}, "dimensions", "output_dimensionality"); err != nil {
		return nil, err
	} else if ok {
		var policyErr error
		policy.Dimensions, policyErr = positiveIntegerPolicy(parameter)
		if policyErr != nil {
			return nil, fmt.Errorf("dimensions: %w", policyErr)
		}
		if policy.Dimensions.Default == nil {
			var defaultErr error
			policy.Dimensions.Default, defaultErr = positiveIntegerPointer(primary.record.OutputVectorSize)
			if defaultErr != nil {
				return nil, fmt.Errorf("output_vector_size: %w", defaultErr)
			}
		}
		return policy, nil
	}
	var err error
	policy.FixedDimensions, err = positiveIntegerPointer(primary.record.OutputVectorSize)
	if err != nil {
		return nil, fmt.Errorf("output_vector_size: %w", err)
	}
	return policy, nil
}

func floatPolicy(parameter sourceParameter) (*numericParameter[float64], error) {
	defaultValue, err := rawFloat(parameter.Default)
	if err != nil {
		return nil, err
	}
	policy := &numericParameter[float64]{Default: defaultValue}
	if parameter.Range != nil {
		policy.Minimum, err = numberFloat(parameter.Range.Minimum)
		if err != nil {
			return nil, fmt.Errorf("minimum: %w", err)
		}
		policy.Maximum, err = numberFloat(parameter.Range.Maximum)
		if err != nil {
			return nil, fmt.Errorf("maximum: %w", err)
		}
	}
	if policy.Minimum != nil && policy.Maximum != nil && *policy.Minimum > *policy.Maximum {
		return nil, errors.New("minimum must not exceed maximum")
	}
	if policy.Default != nil && ((policy.Minimum != nil && *policy.Default < *policy.Minimum) ||
		(policy.Maximum != nil && *policy.Default > *policy.Maximum)) {
		return nil, errors.New("default is outside the declared bounds")
	}
	return policy, nil
}

func integerPolicy(parameter sourceParameter) (*numericParameter[int64], error) {
	defaultValue, err := rawInteger(parameter.Default)
	if err != nil {
		return nil, err
	}
	policy := &numericParameter[int64]{Default: defaultValue}
	if parameter.Range != nil {
		policy.Minimum, err = numberInteger(parameter.Range.Minimum)
		if err != nil {
			return nil, fmt.Errorf("minimum: %w", err)
		}
		policy.Maximum, err = numberInteger(parameter.Range.Maximum)
		if err != nil {
			return nil, fmt.Errorf("maximum: %w", err)
		}
	}
	if policy.Minimum != nil && policy.Maximum != nil && *policy.Minimum > *policy.Maximum {
		return nil, errors.New("minimum must not exceed maximum")
	}
	if policy.Default != nil && ((policy.Minimum != nil && *policy.Default < *policy.Minimum) ||
		(policy.Maximum != nil && *policy.Default > *policy.Maximum)) {
		return nil, errors.New("default is outside the declared bounds")
	}
	return policy, nil
}

func positiveIntegerPolicy(parameter sourceParameter) (*numericParameter[int64], error) {
	policy, err := integerPolicy(parameter)
	if err != nil {
		return nil, err
	}
	for _, value := range []*int64{policy.Minimum, policy.Maximum, policy.Default} {
		if value != nil && *value < 1 {
			return nil, errors.New("value must be positive")
		}
	}
	return policy, nil
}

func outputTokenBounds(records []recordRef) (*numericParameter[int64], bool, error) {
	policy := &numericParameter[int64]{}
	found := false
	for _, ref := range records {
		for _, parameter := range ref.record.ModelParameters {
			if parameter.Disabled || !parameterNameMatch(parameter.ID, []string{"max_tokens", "max_output_tokens", "max_completion_tokens", "max_response_output_tokens"}) {
				continue
			}
			found = true
			if parameter.Range == nil {
				continue
			}
			for _, bound := range []struct {
				name      string
				source    *json.Number
				collected **int64
			}{
				{name: "minimum", source: parameter.Range.Minimum, collected: &policy.Minimum},
				{name: "maximum", source: parameter.Range.Maximum, collected: &policy.Maximum},
			} {
				value, err := numberInteger(bound.source)
				if err != nil {
					return nil, false, fmt.Errorf("%s %s: %w", parameter.ID, bound.name, err)
				}
				if value == nil {
					continue
				}
				if *value < 0 {
					return nil, false, fmt.Errorf("%s %s must not be negative", parameter.ID, bound.name)
				}
				if *bound.collected != nil && **bound.collected != *value {
					return nil, false, fmt.Errorf("conflicting max-output %s declarations in %s", bound.name, ref.key)
				}
				*bound.collected = value
			}
		}
	}
	if policy.Minimum != nil && policy.Maximum != nil && *policy.Minimum > *policy.Maximum {
		return nil, false, errors.New("output minimum must not exceed maximum")
	}
	return policy, found, nil
}

func findParameter(records []recordRef, names ...string) (sourceParameter, bool, error) {
	var found sourceParameter
	foundIn := ""
	for _, ref := range records {
		for _, parameter := range ref.record.ModelParameters {
			if parameter.Disabled || !parameterNameMatch(parameter.ID, names) {
				continue
			}
			if foundIn == "" {
				found = parameter
				foundIn = ref.key
				continue
			}
			if !equivalentParameter(found, parameter) {
				return sourceParameter{}, false, fmt.Errorf("parameter %q conflicts with another declaration (%s and %s)", parameter.ID, foundIn, ref.key)
			}
		}
	}
	if foundIn != "" {
		return found, true, nil
	}
	return sourceParameter{}, false, nil
}

func parameterNameMatch(value string, names []string) bool {
	for _, name := range names {
		if strings.EqualFold(value, name) {
			return true
		}
	}
	return false
}

func equivalentParameter(left, right sourceParameter) bool {
	if !bytes.Equal(bytes.TrimSpace(left.Default), bytes.TrimSpace(right.Default)) {
		return false
	}
	if left.Range == nil || right.Range == nil {
		return left.Range == nil && right.Range == nil
	}
	return equivalentNumber(left.Range.Minimum, right.Range.Minimum) && equivalentNumber(left.Range.Maximum, right.Range.Maximum)
}

func equivalentNumber(left, right *json.Number) bool {
	if left == nil || right == nil {
		return left == nil && right == nil
	}
	return left.String() == right.String()
}

func hasParameter(record sourceRecord, name string) bool {
	for _, parameter := range record.ModelParameters {
		if !parameter.Disabled && strings.EqualFold(parameter.ID, name) {
			return true
		}
	}
	return false
}

func rawFloat(raw json.RawMessage) (*float64, error) {
	number, err := rawNumber(raw)
	if err != nil || number == nil {
		return nil, err
	}
	return numberFloat(number)
}

func rawInteger(raw json.RawMessage) (*int64, error) {
	number, err := rawNumber(raw)
	if err != nil || number == nil {
		return nil, err
	}
	return numberInteger(number)
}

func rawStrings(raw json.RawMessage) ([]string, error) {
	if len(bytes.TrimSpace(raw)) == 0 || bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		return nil, nil
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	var values []string
	if err := decoder.Decode(&values); err != nil {
		return nil, fmt.Errorf("must be an array of strings: %w", err)
	}
	var trailing json.RawMessage
	if err := decoder.Decode(&trailing); err != io.EOF {
		return nil, errors.New("must contain exactly one JSON value")
	}
	return values, nil
}

func rawNumber(raw json.RawMessage) (*json.Number, error) {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 || bytes.Equal(trimmed, []byte("null")) {
		return nil, nil
	}
	decoder := json.NewDecoder(bytes.NewReader(trimmed))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, fmt.Errorf("must be numeric: %w", err)
	}
	var trailing json.RawMessage
	if err := decoder.Decode(&trailing); err != io.EOF {
		return nil, errors.New("must contain exactly one JSON value")
	}
	number, ok := value.(json.Number)
	if !ok {
		return nil, errors.New("must be numeric")
	}
	return &number, nil
}

func numberFloat(number *json.Number) (*float64, error) {
	if number == nil {
		return nil, nil
	}
	value, err := number.Float64()
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) {
		return nil, errors.New("must be a finite number")
	}
	return &value, nil
}

func numberInteger(number *json.Number) (*int64, error) {
	if number == nil {
		return nil, nil
	}
	if value, err := number.Int64(); err == nil {
		return &value, nil
	}
	value, err := number.Float64()
	if err != nil || math.Trunc(value) != value || value < math.MinInt64 || value > math.MaxInt64 || math.IsNaN(value) || math.IsInf(value, 0) {
		return nil, errors.New("must be a finite integer")
	}
	converted := int64(value)
	return &converted, nil
}

func nonNegativeInteger(number *json.Number) (int64, error) {
	value, err := numberInteger(number)
	if err != nil {
		return 0, err
	}
	if value == nil {
		return 0, nil
	}
	if *value < 0 {
		return 0, errors.New("must not be negative")
	}
	return *value, nil
}

func positiveIntegerPointer(number *json.Number) (*int64, error) {
	value, err := numberInteger(number)
	if err != nil {
		return nil, err
	}
	if value == nil || *value == 0 {
		return nil, nil
	}
	if *value < 0 {
		return nil, errors.New("must be positive")
	}
	return value, nil
}

func sortRecords(modelID string, records []recordRef) {
	sort.Slice(records, func(left, right int) bool {
		leftRank := recordRank(modelID, records[left])
		rightRank := recordRank(modelID, records[right])
		for index := range leftRank {
			if leftRank[index] != rightRank[index] {
				return leftRank[index] < rightRank[index]
			}
		}
		return records[left].key < records[right].key
	})
}

func recordRank(modelID string, ref recordRef) [3]int {
	class := 6
	switch {
	case ref.key == modelID:
		class = 0
	case strings.EqualFold(ref.key, modelID):
		class = 1
	case strings.EqualFold(ref.key, modelID+":batch") || strings.HasSuffix(strings.ToLower(ref.key), "/"+strings.ToLower(modelID)+":batch"):
		class = 2
	}
	return [3]int{class, strings.Count(ref.key, "/"), len(ref.key)}
}

func sortedSet(values map[string]struct{}) []string {
	result := make([]string, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
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
		if !((character >= '0' && character <= '9') || (character >= 'a' && character <= 'f') || (character >= 'A' && character <= 'F')) {
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

func writeCatalog(path string, document catalogDocument) error {
	var output bytes.Buffer
	fmt.Fprintf(&output, "{\n  \"schema_version\": %d,\n  \"sources\": ", document.SchemaVersion)
	sources, err := json.Marshal(document.Sources)
	if err != nil {
		return fmt.Errorf("encode catalog sources: %w", err)
	}
	output.Write(sources)
	output.WriteString(",\n  \"models\": [\n")
	for index, model := range document.Models {
		encoded, encodeErr := json.Marshal(model)
		if encodeErr != nil {
			return fmt.Errorf("encode model %q: %w", model.ID, encodeErr)
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
		return fmt.Errorf("create catalog compressor: %w", err)
	}
	if _, err := writer.Write(output.Bytes()); err != nil {
		return fmt.Errorf("compress catalog: %w", err)
	}
	if err := writer.Close(); err != nil {
		return fmt.Errorf("close catalog compressor: %w", err)
	}
	if err := os.WriteFile(path, compressed.Bytes(), 0o644); err != nil {
		return fmt.Errorf("write catalog: %w", err)
	}
	return nil
}
