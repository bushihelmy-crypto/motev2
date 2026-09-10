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
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

const catalogSchemaVersion = 1

// defaultMaxOutputTokens is the Gateway-wide target default for generated
// text output. compileModel clamps it to a model's known token limits so the
// effective default remains valid for models with a smaller ceiling.
const defaultMaxOutputTokens int64 = 4096

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
	ID      string          `json:"id"`
	Default json.RawMessage `json:"default"`
	Range   *sourceRange    `json:"range"`
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
	Default *int64 `json:"default,omitempty"`
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
	flag.StringVar(&result.newAPIRef, "new-api-ref", "origin/main", "new-api Git ref to read")
	flag.StringVar(&result.bifrostRepo, "bifrost-repo", "", "path to the Bifrost Git repository")
	flag.StringVar(&result.bifrostRef, "bifrost-ref", "origin/dev", "Bifrost Git ref recorded as provenance")
	flag.StringVar(&result.modelParameters, "model-parameters", "", "Bifrost model-parameters JSON snapshot")
	flag.StringVar(&result.output, "output", "src/internal/model/catalog_data.json.gz", "gzip-compressed catalog output path")
	flag.Parse()
	return result
}

func run(configured options) error {
	if configured.newAPIRepo == "" || configured.bifrostRepo == "" || configured.modelParameters == "" {
		return errors.New("-new-api-repo, -bifrost-repo, and -model-parameters are required")
	}
	newRevision, err := gitOutput(configured.newAPIRepo, "rev-parse", configured.newAPIRef)
	if err != nil {
		return fmt.Errorf("resolve new-api ref: %w", err)
	}
	bifrostRevision, err := gitOutput(configured.bifrostRepo, "rev-parse", configured.bifrostRef)
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
	models := compileModels(records, newModels)
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
	fmt.Printf("wrote %d models to %s\n", len(models), configured.output)
	return nil
}

func decodeSourceRecords(data []byte) ([]recordRef, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var source map[string]sourceRecord
	if err := decoder.Decode(&source); err != nil {
		return nil, fmt.Errorf("decode Bifrost model parameters: %w", err)
	}
	records := make([]recordRef, 0, len(source))
	for key, record := range source {
		records = append(records, recordRef{key: key, record: record})
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

func compileModels(records []recordRef, newModels map[string]struct{}) []modelConfig {
	byBase := make(map[string][]recordRef)
	byFoldedBase := make(map[string][]recordRef)
	byKey := make(map[string]recordRef, len(records))
	candidateIDs := make(map[string]struct{})
	for _, ref := range records {
		byKey[ref.key] = ref
		if _, supported := supportedModes[ref.record.Mode]; !supported || ref.record.BaseModel == "" || syntheticModelID(ref.record.BaseModel) {
			continue
		}
		byBase[ref.record.BaseModel] = append(byBase[ref.record.BaseModel], ref)
		folded := strings.ToLower(ref.record.BaseModel)
		byFoldedBase[folded] = append(byFoldedBase[folded], ref)
		candidateIDs[ref.record.BaseModel] = struct{}{}
	}
	for modelID := range newModels {
		if inferOperation(modelID) != "" && !syntheticModelID(modelID) {
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
	for _, modelID := range ids {
		matches := append([]recordRef(nil), byBase[modelID]...)
		if len(matches) == 0 {
			matches = append(matches, byFoldedBase[strings.ToLower(modelID)]...)
		}
		if exact, ok := byKey[modelID]; ok {
			matches = append(matches, exact)
		}
		if _, listedByNewAPI := newModels[modelID]; listedByNewAPI || len(matches) == 0 {
			for _, ref := range records {
				if strings.EqualFold(ref.key, modelID) || hasModelSuffix(ref.key, modelID) {
					matches = append(matches, ref)
				}
			}
		}
		matches = uniqueRecords(matches)
		sortRecords(modelID, matches)
		model, ok := compileModel(modelID, matches, byBase, byFoldedBase)
		if ok {
			models = append(models, model)
		}
	}
	return models
}

func compileModel(
	modelID string,
	matches []recordRef,
	byBase map[string][]recordRef,
	byFoldedBase map[string][]recordRef,
) (modelConfig, bool) {
	if len(matches) == 0 {
		operation := inferOperation(modelID)
		if operation == "" {
			return modelConfig{}, false
		}
		return modelConfig{
			ID:         modelID,
			Lifecycle:  "active",
			Operations: []operationConfig{compileOperation(operation, "", nil, recordRef{})},
		}, true
	}
	if inferred := inferOperation(modelID); inferred != "" && inferred != "generate" {
		preferred := make([]recordRef, 0, len(matches))
		for _, ref := range matches {
			if operationForRecord(modelID, ref) == inferred {
				preferred = append(preferred, ref)
			}
		}
		if len(preferred) > 0 {
			matches = preferred
		}
	}

	primary := matches[0]
	operation := operationForRecord(modelID, primary)
	if inferred := inferOperation(modelID); inferred != "" && inferred != "generate" {
		operation = inferred
	}
	if operation == "" {
		return modelConfig{}, false
	}
	supplemental := append([]recordRef(nil), matches...)
	base := primary.record.BaseModel
	if base != "" {
		supplemental = append(supplemental, byBase[base]...)
		if len(byBase[base]) == 0 {
			supplemental = append(supplemental, byFoldedBase[strings.ToLower(base)]...)
		}
	}
	supplemental = uniqueRecords(supplemental)
	filtered := supplemental[:0]
	for _, ref := range supplemental {
		if operationForRecord(modelID, ref) == operation {
			filtered = append(filtered, ref)
		}
	}
	supplemental = filtered
	sortRecords(modelID, supplemental)

	limits := compileTokenLimits(operation, primary)
	compiledOperation := compileOperation(operation, primary.record.Mode, supplemental, primary)
	if compiledOperation.Generation != nil && compiledOperation.Generation.MaxOutputTokens != nil {
		defaultValue := compiledOperation.Generation.MaxOutputTokens.Default
		if defaultValue != nil {
			if limits.MinOutputTokens > 0 && *defaultValue < limits.MinOutputTokens {
				*defaultValue = limits.MinOutputTokens
			}
			if limits.MaxOutputTokens > 0 && *defaultValue > limits.MaxOutputTokens {
				*defaultValue = limits.MaxOutputTokens
			}
		}
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
	}, true
}

func operationForRecord(modelID string, ref recordRef) string {
	inferred := inferOperation(modelID)
	if inferred == "" {
		return ""
	}
	if inferred != "generate" {
		return inferred
	}
	operation := supportedModes[ref.record.Mode]
	switch operation {
	case "generate":
		return "generate"
	case "image_generation":
		if imageOperationEvidence(modelID, ref) {
			return "image_generation"
		}
		return "generate"
	case "video_generation":
		if videoOperationEvidence(modelID, ref) {
			return "video_generation"
		}
		return "generate"
	case "audio_generation":
		return "audio_generation"
	case "audio_transcription":
		return "audio_transcription"
	case "realtime":
		return "realtime"
	case "embedding":
		return "embedding"
	case "rerank":
		return "rerank"
	default:
		return ""
	}
}

func syntheticModelID(modelID string) bool {
	value := strings.ToLower(modelID)
	if syntheticRequestPreset(value) {
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

func compileTokenLimits(operation string, primary recordRef) tokenLimits {
	maxInput, _ := positiveInteger(primary.record.MaxInputTokens)
	if maxInput == 0 && operation == "embedding" {
		maxInput, _ = positiveInteger(primary.record.MaxTokens)
	}
	limits := tokenLimits{MaxInputTokens: maxInput}
	if operation != "generate" && operation != "realtime" {
		return limits
	}
	maxOutput, _ := positiveInteger(primary.record.MaxOutputTokens)
	if maxOutput == 0 {
		maxOutput, _ = positiveInteger(primary.record.MaxTokens)
	}
	parameter, parameterFound := findParameter([]recordRef{primary}, "max_tokens", "max_output_tokens", "max_completion_tokens", "max_response_output_tokens")
	if maxOutput == 0 && parameterFound && parameter.Range != nil {
		maxOutput, _ = positiveInteger(parameter.Range.Maximum)
	}
	limits.MaxOutputTokens = maxOutput
	if maxOutput == 0 {
		return limits
	}
	limits.MinOutputTokens = 1
	if parameterFound && parameter.Range != nil {
		if minimum, valid := positiveInteger(parameter.Range.Minimum); valid {
			limits.MinOutputTokens = minimum
		}
	}
	return limits
}

func compileOperation(operation, sourceMode string, records []recordRef, primary recordRef) operationConfig {
	primaryOnly := []recordRef{primary}
	if primary.key == "" {
		primaryOnly = nil
	}
	inputs, outputs := compileModalities(operation, sourceMode, primaryOnly)
	result := operationConfig{
		Operation:        operation,
		Modes:            compileModes(operation, primaryOnly),
		InputModalities:  inputs,
		OutputModalities: outputs,
		Features:         compileFeatures(operation, primaryOnly),
	}
	if operation == "generate" || operation == "realtime" {
		result.Generation = compileGenerationPolicy(primaryOnly, primary)
	}
	if operation == "embedding" {
		result.Embedding = compileEmbeddingPolicy(records, primary)
	}
	return result
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
				continue
			}
			batchOnly = false
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

func compileGenerationPolicy(records []recordRef, primary recordRef) *generationPolicy {
	policy := &generationPolicy{}
	samplingAllowed := primary.record.SupportsSamplingParams == nil || *primary.record.SupportsSamplingParams
	if samplingAllowed {
		if parameter, ok := findParameter(records, "temperature"); ok {
			policy.Temperature = floatPolicy(parameter)
		}
		if parameter, ok := findParameter(records, "top_p", "topp"); ok {
			policy.TopP = floatPolicy(parameter)
		}
		if parameter, ok := findParameter(records, "seed"); ok {
			policy.Seed = integerPolicy(parameter)
		}
	}
	_, parameterFound := findParameter(records, "max_tokens", "max_output_tokens", "max_completion_tokens", "max_response_output_tokens")
	_, maximumKnown := positiveInteger(primary.record.MaxOutputTokens)
	if parameterFound || maximumKnown {
		defaultValue := defaultMaxOutputTokens
		policy.MaxOutputTokens = &outputTokenParameter{Default: &defaultValue}
	}
	if parameter, ok := findParameter(records, "stop", "stop_sequences", "stopsequences"); ok {
		policy.Stop = &stopParameter{Default: rawStrings(parameter.Default)}
	}
	if policy.Temperature == nil && policy.TopP == nil && policy.MaxOutputTokens == nil && policy.Stop == nil && policy.Seed == nil {
		return nil
	}
	return policy
}

func compileEmbeddingPolicy(records []recordRef, primary recordRef) *embeddingPolicy {
	policy := &embeddingPolicy{}
	if parameter, ok := findParameter(records, "dimensions", "output_dimensionality"); ok {
		policy.Dimensions = integerPolicy(parameter)
		if policy.Dimensions.Default == nil {
			policy.Dimensions.Default, _ = positiveIntegerPointer(primary.record.OutputVectorSize)
		}
		return policy
	}
	policy.FixedDimensions, _ = positiveIntegerPointer(primary.record.OutputVectorSize)
	if policy.FixedDimensions == nil {
		for _, ref := range records {
			if value, valid := positiveIntegerPointer(ref.record.OutputVectorSize); valid {
				policy.FixedDimensions = value
				break
			}
		}
	}
	return policy
}

func floatPolicy(parameter sourceParameter) *numericParameter[float64] {
	policy := &numericParameter[float64]{Default: rawFloat(parameter.Default)}
	if parameter.Range != nil {
		policy.Minimum = numberFloat(parameter.Range.Minimum)
		policy.Maximum = numberFloat(parameter.Range.Maximum)
	}
	if policy.Minimum != nil && policy.Maximum != nil && *policy.Minimum > *policy.Maximum {
		policy.Minimum, policy.Maximum = nil, nil
	}
	if policy.Default != nil && ((policy.Minimum != nil && *policy.Default < *policy.Minimum) ||
		(policy.Maximum != nil && *policy.Default > *policy.Maximum)) {
		policy.Default = nil
	}
	return policy
}

func integerPolicy(parameter sourceParameter) *numericParameter[int64] {
	policy := &numericParameter[int64]{Default: rawInteger(parameter.Default)}
	if parameter.Range != nil {
		policy.Minimum = numberInteger(parameter.Range.Minimum)
		policy.Maximum = numberInteger(parameter.Range.Maximum)
	}
	if policy.Minimum != nil && policy.Maximum != nil && *policy.Minimum > *policy.Maximum {
		policy.Minimum, policy.Maximum = nil, nil
	}
	if policy.Default != nil && ((policy.Minimum != nil && *policy.Default < *policy.Minimum) ||
		(policy.Maximum != nil && *policy.Default > *policy.Maximum)) {
		policy.Default = nil
	}
	return policy
}

func findParameter(records []recordRef, names ...string) (sourceParameter, bool) {
	wanted := make(map[string]struct{}, len(names))
	for _, name := range names {
		wanted[name] = struct{}{}
	}
	for _, ref := range records {
		for _, parameter := range ref.record.ModelParameters {
			if _, ok := wanted[strings.ToLower(parameter.ID)]; ok {
				return parameter, true
			}
		}
	}
	return sourceParameter{}, false
}

func hasParameter(record sourceRecord, name string) bool {
	for _, parameter := range record.ModelParameters {
		if strings.EqualFold(parameter.ID, name) {
			return true
		}
	}
	return false
}

func rawFloat(raw json.RawMessage) *float64 {
	if len(raw) == 0 || bytes.Equal(raw, []byte("null")) {
		return nil
	}
	var number json.Number
	if json.Unmarshal(raw, &number) != nil {
		return nil
	}
	value, err := number.Float64()
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) {
		return nil
	}
	return &value
}

func rawInteger(raw json.RawMessage) *int64 {
	if len(raw) == 0 || bytes.Equal(raw, []byte("null")) {
		return nil
	}
	var number json.Number
	if json.Unmarshal(raw, &number) != nil {
		return nil
	}
	return numberInteger(&number)
}

func rawStrings(raw json.RawMessage) []string {
	if len(raw) == 0 || bytes.Equal(raw, []byte("null")) {
		return nil
	}
	var values []string
	if json.Unmarshal(raw, &values) != nil {
		return nil
	}
	return values
}

func numberFloat(number *json.Number) *float64 {
	if number == nil {
		return nil
	}
	value, err := number.Float64()
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) {
		return nil
	}
	return &value
}

func numberInteger(number *json.Number) *int64 {
	if number == nil {
		return nil
	}
	if value, err := number.Int64(); err == nil {
		return &value
	}
	value, err := number.Float64()
	if err != nil || math.Trunc(value) != value || value < math.MinInt64 || value > math.MaxInt64 {
		return nil
	}
	converted := int64(value)
	return &converted
}

func positiveInteger(number *json.Number) (int64, bool) {
	value := numberInteger(number)
	if value == nil || *value <= 0 {
		return 0, false
	}
	return *value, true
}

func positiveIntegerPointer(number *json.Number) (*int64, bool) {
	value, valid := positiveInteger(number)
	if !valid {
		return nil, false
	}
	return &value, true
}

func hasModelSuffix(key, modelID string) bool {
	keyFolded := strings.ToLower(key)
	modelFolded := strings.ToLower(modelID)
	return strings.HasSuffix(keyFolded, "/"+modelFolded)
}

func uniqueRecords(records []recordRef) []recordRef {
	seen := make(map[string]struct{}, len(records))
	result := make([]recordRef, 0, len(records))
	for _, ref := range records {
		if _, exists := seen[ref.key]; exists {
			continue
		}
		seen[ref.key] = struct{}{}
		result = append(result, ref)
	}
	return result
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
	case strings.HasSuffix(ref.key, "/"+modelID):
		class = 2
	case hasModelSuffix(ref.key, modelID):
		class = 3
	case ref.record.BaseModel == modelID:
		class = 4
	case strings.EqualFold(ref.record.BaseModel, modelID):
		class = 5
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
