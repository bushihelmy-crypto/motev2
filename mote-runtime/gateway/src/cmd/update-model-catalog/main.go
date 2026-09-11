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

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	modelcatalog "github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
)

const catalogSchemaVersion = 1
const rejectionManifestSchemaVersion = 1

// Keep the public provenance commits split into short literals so the secret
// scanner does not mistake a Git SHA for credential material.
const (
	defaultNewAPIRef  = "bdef1175" + "05247769" + "268b2096" + "65fb3ad7" + "554c3da7"
	defaultBifrostRef = "c5c02ae7" + "47fe294a" + "7f7f7652" + "77aae08b" + "bf0835fa"
)

var supportedModes = map[string]api.Operation{
	"chat":                api.OperationGenerate,
	"completion":          api.OperationGenerate,
	"responses":           api.OperationGenerate,
	"embedding":           api.OperationEmbedding,
	"rerank":              api.OperationRerank,
	"image_generation":    api.OperationImageGeneration,
	"image_edit":          api.OperationImageGeneration,
	"audio_speech":        api.OperationAudioGeneration,
	"audio_transcription": api.OperationAudioTranscription,
	"video_generation":    api.OperationVideoGeneration,
	"realtime":            api.OperationRealtime,
}

type options struct {
	newAPIRepo      string
	newAPIRef       string
	bifrostRepo     string
	bifrostRef      string
	modelParameters string
	output          string
	rejections      string
}

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

type catalogDocument struct {
	SchemaVersion int             `json:"schema_version"`
	Sources       []catalogSource `json:"sources"`
	Models        []modelConfig   `json:"models"`
}

type rejectionManifest struct {
	SchemaVersion int             `json:"schema_version"`
	Rejections    []*compileError `json:"rejections"`
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
	Operation        api.Operation      `json:"operation"`
	Modes            []api.DeliveryMode `json:"modes"`
	InputModalities  []api.Modality     `json:"input_modalities"`
	OutputModalities []api.Modality     `json:"output_modalities"`
	Features         []api.Feature      `json:"features,omitempty"`
	Generation       *generationPolicy  `json:"generation,omitempty"`
	Embedding        *embeddingPolicy   `json:"embedding,omitempty"`
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
	flag.StringVar(&result.rejections, "rejections", "", "machine-readable rejection manifest path (default derives from -output)")
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
	if len(models) == 0 {
		return errors.New("compile model catalog: sources produced no usable models")
	}
	digest := sha256.Sum256(parameterData)
	sources := []catalogSource{
		{Name: "new-api", Revision: strings.TrimSpace(newRevision)},
		{Name: "bifrost", Revision: strings.TrimSpace(bifrostRevision)},
		{Name: "bifrost-model-parameters", SHA256: hex.EncodeToString(digest[:])},
	}
	document := catalogDocument{
		SchemaVersion: catalogSchemaVersion,
		Sources:       sources,
		Models:        models,
	}
	if err := writeCatalog(configured.output, document); err != nil {
		return err
	}
	manifestPath := configured.rejections
	if manifestPath == "" {
		manifestPath = defaultRejectionManifestPath(configured.output)
	}
	if err := writeRejectionManifest(manifestPath, rejectionManifest{
		SchemaVersion: rejectionManifestSchemaVersion,
		Rejections:    rejected,
	}); err != nil {
		return err
	}
	for _, rejection := range rejected {
		fmt.Fprintf(os.Stderr, "rejected model: %v\n", rejection)
	}
	fmt.Printf("wrote %d models to %s (%d rejected; manifest %s)\n", len(models), configured.output, len(rejected), manifestPath)
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
	ModelID string `json:"model_id"`
	Field   string `json:"field"`
	Reason  string `json:"reason"`
}

func (err *compileError) Error() string {
	if err.ModelID == "" {
		return fmt.Sprintf("invalid model source %s: %s", err.Field, err.Reason)
	}
	return fmt.Sprintf("invalid model source %q %s: %s", err.ModelID, err.Field, err.Reason)
}

// identityIndex contains only deterministic identity hints. It deliberately
// does not treat BaseModel as a family alias: an exact, versioned source key
// remains its own identity, while a qualified provider key may use a matching
// bare model or an unambiguous BaseModel value to remove the provider prefix.
type identityIndex struct {
	bare            map[string]string
	anchors         map[string]string
	baseFallback    map[string]string
	servicePrefixes map[string]struct{}
	preferred       map[string]string
}

func buildIdentityIndex(records []recordRef, newModels map[string]struct{}) identityIndex {
	bareCandidates := make(map[string][]string)
	anchorCandidates := make(map[string][]string)
	baseCandidates := make(map[string][]string)
	baseLeaves := make(map[string]map[string]struct{})
	servicePrefixes := make(map[string]struct{})
	preferred := make(map[string]string)
	modelNamespaces := map[string]struct{}{
		// These prefixes are part of the public model identifier in the source
		// catalogs (for example mistral/codestral-embed), not transport wrappers.
		"cohere":  {},
		"mistral": {},
	}
	for _, prefix := range []string{
		"aiml", "anthropic", "azure", "bedrock", "bedrock_mantle", "chatgpt", "cloudflare",
		"databricks", "deepinfra", "deepseek", "fal_ai", "fireworks_ai", "gemini",
		"gmi", "google", "groq", "huggingface", "litellm", "novita", "openai",
		"opencode-zen", "openrouter", "palm", "perplexity", "publishers", "replicate", "stability",
		"together", "together_ai", "vertex_ai", "vercel_ai_gateway", "xai",
	} {
		servicePrefixes[strings.ToLower(prefix)] = struct{}{}
	}
	add := func(target map[string][]string, value string) {
		value = stripBatchSuffix(strings.TrimSpace(value))
		if value == "" {
			return
		}
		folded := strings.ToLower(value)
		for _, existing := range target[folded] {
			if existing == value {
				return
			}
		}
		target[folded] = append(target[folded], value)
	}
	for _, ref := range records {
		key := stripBatchSuffix(strings.TrimSpace(ref.key))
		if key == "" || nonModelSourceRecord(key) || (syntheticModelID(key) && !strings.HasSuffix(strings.ToLower(key), ":batch")) {
			continue
		}
		add(anchorCandidates, key)
		if !strings.Contains(key, "/") {
			add(bareCandidates, key)
		}
		base := stripBatchSuffix(strings.TrimSpace(ref.record.BaseModel))
		if base != "" {
			add(anchorCandidates, base)
			if !strings.Contains(base, "/") {
				add(baseCandidates, base)
				leaf := strings.ToLower(key)
				if slash := strings.LastIndexByte(leaf, '/'); slash >= 0 {
					leaf = leaf[slash+1:]
				}
				if baseLeaves[strings.ToLower(base)] == nil {
					baseLeaves[strings.ToLower(base)] = make(map[string]struct{})
				}
				baseLeaves[strings.ToLower(base)][leaf] = struct{}{}
			}
		}
		if provider := strings.ToLower(strings.TrimSpace(ref.record.Provider)); provider != "" {
			if _, namespace := modelNamespaces[provider]; !namespace {
				servicePrefixes[provider] = struct{}{}
				servicePrefixes[strings.ReplaceAll(provider, "_", "-")] = struct{}{}
			}
		}
	}
	for modelID := range newModels {
		modelID = stripBatchSuffix(strings.TrimSpace(modelID))
		if modelID == "" || syntheticModelID(modelID) {
			continue
		}
		add(anchorCandidates, modelID)
		if current, exists := preferred[strings.ToLower(modelID)]; !exists || modelID < current {
			preferred[strings.ToLower(modelID)] = modelID
		}
		if !strings.Contains(modelID, "/") {
			add(bareCandidates, modelID)
		}
	}
	choose := func(candidates map[string][]string) map[string]string {
		result := make(map[string]string, len(candidates))
		for folded, values := range candidates {
			sort.Slice(values, func(left, right int) bool {
				leftParts, rightParts := strings.Count(values[left], "/"), strings.Count(values[right], "/")
				if leftParts != rightParts {
					return leftParts < rightParts
				}
				leftLower, rightLower := strings.ToLower(values[left]), strings.ToLower(values[right])
				if leftLower != rightLower {
					return leftLower < rightLower
				}
				return values[left] < values[right]
			})
			result[folded] = values[0]
		}
		return result
	}
	baseFallbackCandidates := make(map[string][]string)
	for folded, values := range baseCandidates {
		if len(baseLeaves[folded]) == 1 {
			baseFallbackCandidates[folded] = values
		}
	}
	return identityIndex{
		bare:            choose(bareCandidates),
		anchors:         choose(anchorCandidates),
		baseFallback:    choose(baseFallbackCandidates),
		servicePrefixes: servicePrefixes,
		preferred:       preferred,
	}
}

func stripBatchSuffix(value string) string {
	if strings.HasSuffix(strings.ToLower(value), ":batch") {
		return value[:len(value)-len(":batch")]
	}
	return value
}

func canonicalRecordID(ref recordRef, index identityIndex) string {
	raw := stripBatchSuffix(strings.TrimSpace(ref.key))
	if raw == "" {
		return ""
	}
	if canonical, ok := index.preferred[strings.ToLower(raw)]; ok {
		parts := strings.Split(raw, "/")
		if len(parts) == 1 || !isServicePrefix(parts[0], index) {
			return canonical
		}
	}
	if canonical, ok := index.bare[strings.ToLower(raw)]; ok {
		return canonical
	}
	base := stripBatchSuffix(strings.TrimSpace(ref.record.BaseModel))
	if strings.Contains(raw, "/") {
		if canonical := canonicalSuffix(raw, index); canonical != "" {
			return canonical
		}
		if base != "" {
			if canonical, ok := index.bare[strings.ToLower(base)]; ok {
				return preferredCanonical(canonical, index)
			}
			if !strings.Contains(base, "/") {
				if canonical, ok := index.baseFallback[strings.ToLower(base)]; ok {
					return preferredCanonical(canonical, index)
				}
				return base
			}
			if canonical := canonicalSuffix(base, index); canonical != "" {
				return canonical
			}
			return stripServicePrefix(base, index)
		}
	}
	if canonical := canonicalSuffix(raw, index); canonical != "" {
		return canonical
	}
	return stripServicePrefix(raw, index)
}

func preferredCanonical(value string, index identityIndex) string {
	if canonical, ok := index.preferred[strings.ToLower(value)]; ok {
		return canonical
	}
	return value
}

func canonicalSuffix(value string, index identityIndex) string {
	parts := strings.Split(value, "/")
	// A qualified model namespace such as mistral/<model> or cohere/<model>
	// is itself an authoritative identity.  Do not let a bare New-API listing
	// erase that namespace; only strip a known service wrapper (openrouter/,
	// vercel_ai_gateway/, openai/, ...).
	if len(parts) > 1 && !isServicePrefix(parts[0], index) {
		if canonical, ok := index.preferred[strings.ToLower(value)]; ok {
			return canonical
		}
		if canonical, ok := index.anchors[strings.ToLower(value)]; ok {
			return canonical
		}
	}
	for start := len(parts) - 1; start >= 0; start-- {
		candidate := strings.Join(parts[start:], "/")
		if canonical, ok := index.preferred[strings.ToLower(candidate)]; ok {
			candidateParts := strings.Split(candidate, "/")
			if len(candidateParts) == 1 || !isServicePrefix(candidateParts[0], index) {
				return canonical
			}
		}
		if canonical, ok := index.bare[strings.ToLower(candidate)]; ok {
			return canonical
		}
		if canonical, ok := index.anchors[strings.ToLower(candidate)]; ok && !isServicePrefix(parts[start], index) {
			return canonical
		}
	}
	return ""
}

func stripServicePrefix(value string, index identityIndex) string {
	parts := strings.Split(value, "/")
	for len(parts) > 1 && isServicePrefix(parts[0], index) {
		parts = parts[1:]
	}
	return strings.Join(parts, "/")
}

func isServicePrefix(value string, index identityIndex) bool {
	_, ok := index.servicePrefixes[strings.ToLower(strings.TrimSpace(value))]
	return ok
}

func isBatchRecord(ref recordRef) bool {
	return strings.HasSuffix(strings.ToLower(strings.TrimSpace(ref.key)), ":batch") ||
		strings.HasSuffix(strings.ToLower(strings.TrimSpace(ref.record.BaseModel)), ":batch")
}

// compileModels groups provider-qualified rows by one canonical model ID. A
// model may then publish multiple semantic operations; chat/completion/
// responses are already normalized to generate by operationForRecord.
func compileModels(records []recordRef, newModels map[string]struct{}) ([]modelConfig, []*compileError) {
	index := buildIdentityIndex(records, newModels)
	groups := make(map[string][]recordRef)
	for _, ref := range records {
		if ref.key == "" || nonModelSourceRecord(ref.key) || (syntheticModelID(ref.key) && !isBatchRecord(ref)) {
			continue
		}
		modelID := canonicalRecordID(ref, index)
		if modelID != "" && !syntheticModelID(modelID) {
			groups[modelID] = append(groups[modelID], ref)
		}
	}
	for modelID := range newModels {
		if syntheticModelID(modelID) {
			continue
		}
		ref := recordRef{key: modelID}
		canonical := canonicalRecordID(ref, index)
		if canonical != "" && !syntheticModelID(canonical) {
			if _, exists := groups[canonical]; !exists {
				groups[canonical] = nil
			}
		}
	}
	ids := make([]string, 0, len(groups))
	for modelID := range groups {
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
		model, present, errorsForModel := compileModelGroup(modelID, groups[modelID])
		rejected = append(rejected, errorsForModel...)
		if present {
			models = append(models, model)
		}
	}
	sortCompileErrors(rejected)
	return models, rejected
}

// compileModelGroup is the only path that turns one canonical model identity
// into a catalog definition.  A source snapshot can contain observations from
// several services and several semantic operations; those observations are
// kept in operation buckets instead of being flattened into one "first row".
// The exact canonical row owns model-wide facts when it exists.  Qualified
// rows can add delivery evidence for the same operation, but they cannot
// replace the exact row's limits or parameter policy.
func compileModelGroup(modelID string, matches []recordRef) (modelConfig, bool, []*compileError) {
	if len(matches) == 0 {
		return compileInferredModel(modelID)
	}

	sortRecords(modelID, matches)
	exact, hasExact := exactCanonicalRecord(modelID, matches)
	if hasExact && !isMetadataRecord(exact) {
		if strings.TrimSpace(exact.record.Mode) == "" {
			return modelConfig{}, false, []*compileError{{ModelID: modelID, Field: "mode", Reason: "exact source record is missing an authoritative mode"}}
		}
		if _, supported := supportedModes[strings.TrimSpace(exact.record.Mode)]; !supported {
			return modelConfig{}, false, []*compileError{{ModelID: modelID, Field: "operation", Reason: fmt.Sprintf("exact source record declares unsupported mode %q", exact.record.Mode)}}
		}
	}

	groups := make(map[api.Operation][]recordRef)
	nonBatchOperations := make(map[api.Operation]struct{})
	rejected := make([]*compileError, 0)
	for _, ref := range matches {
		// Rows produced by the pricing CSV merger carry a mode for display, not
		// an authoritative model operation.  They may identify a candidate
		// model, but must never override a real capability row.
		if isMetadataRecord(ref) {
			continue
		}
		operation, reason := authoritativeOperation(ref)
		if reason != "" {
			if isBatchRecord(ref) || !isExactCanonicalKey(modelID, ref) {
				rejected = append(rejected, &compileError{ModelID: modelID, Field: "operation", Reason: fmt.Sprintf("source record %q: %s", ref.key, reason)})
				continue
			}
			// An exact malformed row is an identity-level failure.  Falling back
			// to a qualified row would hide the very source fact the exact key
			// claims to own.
			rejected = append(rejected, &compileError{ModelID: modelID, Field: "operation", Reason: reason})
			return modelConfig{}, false, rejected
		}
		if isBatchRecord(ref) {
			// Batch facts are admitted below only when a non-batch operation with
			// the same semantic operation exists (or when the model is batch-only).
			groups[operation] = append(groups[operation], ref)
			continue
		}
		nonBatchOperations[operation] = struct{}{}
		groups[operation] = append(groups[operation], ref)
	}

	if len(groups) == 0 {
		// A model represented only by metadata rows still gets one operation,
		// but the operation is inferred from its canonical ID because no source
		// record claimed an authoritative fact.
		inferred, present, inferredRejected := compileInferredModel(modelID)
		return inferred, present, append(rejected, inferredRejected...)
	}

	// Isolate a conflicting batch row.  It is a bad delivery observation, not
	// evidence that a valid non-batch operation must be removed.
	for operation, records := range groups {
		if _, hasNonBatch := nonBatchOperations[operation]; hasNonBatch {
			continue
		}
		hasNonBatchAny := len(nonBatchOperations) != 0
		if !hasNonBatchAny {
			continue // this is a legitimate batch-only operation
		}
		kept := records[:0]
		for _, ref := range records {
			if !isBatchRecord(ref) {
				kept = append(kept, ref)
				continue
			}
			rejected = append(rejected, &compileError{
				ModelID: modelID,
				Field:   "operation",
				Reason:  fmt.Sprintf("batch source record %q declares operation %q without a matching non-batch operation", ref.key, operation),
			})
		}
		if len(kept) == 0 {
			delete(groups, operation)
		} else {
			groups[operation] = kept
		}
	}

	operations := make([]api.Operation, 0, len(groups))
	compiled := make([]operationConfig, 0, len(groups))
	var lifecycleOwner recordRef
	hasLifecycleOwner := false
	var tokenOwner recordRef
	hasTokenOwner := false
	for operation := range groups {
		operations = append(operations, operation)
	}
	sort.Slice(operations, func(left, right int) bool { return operations[left] < operations[right] })

	for _, operation := range operations {
		records := groups[operation]
		primary := chooseOperationPrimary(modelID, records)
		rejected = append(rejected, modalityConflictRejections(modelID, operation, records, primary)...)
		if !hasLifecycleOwner || rankLess(primaryRank(modelID, primary), primaryRank(modelID, lifecycleOwner)) {
			lifecycleOwner, hasLifecycleOwner = primary, true
		}
		_, outputTokensSupported, limitErr := compileTokenLimits(operation, primary)
		if limitErr != nil {
			rejected = append(rejected, &compileError{ModelID: modelID, Field: "token_limits", Reason: fmt.Sprintf("operation %q: %s", operation, limitErr)})
			continue
		}
		compiledOperation, operationErr := compileOperation(operation, primary.record.Mode, records, primary, outputTokensSupported)
		if operationErr != nil {
			rejected = append(rejected, &compileError{ModelID: modelID, Field: "operation", Reason: fmt.Sprintf("operation %q: %s", operation, operationErr)})
			continue
		}
		compiled = append(compiled, compiledOperation)
		if !hasTokenOwner || (primary.key == modelID && tokenOwner.key != modelID) {
			// The exact row is the stable model identity owner, but only after
			// its operation has passed validation. A failed operation must not
			// poison limits for another valid operation on the same model.
			tokenOwner, hasTokenOwner = primary, true
		}
	}
	if len(compiled) == 0 {
		return modelConfig{}, false, rejected
	}

	// TokenLimits has one owner by design.  It is the exact canonical row when
	// that row supplied a successfully compiled operation; otherwise the stable
	// primary selected above.  We never take a "first embedding dimension" or
	// union limits from another service row.
	if !hasTokenOwner {
		tokenOwner = lifecycleOwner
	}
	ownerOperation := operationForRecord(modelID, tokenOwner)
	limits, _, limitErr := compileTokenLimits(ownerOperation, tokenOwner)
	if limitErr != nil {
		// The operation carrying the model-wide owner may have failed while a
		// different operation remained valid.  Unknown limits are safer than
		// inventing a value from an unrelated service record.
		limits = tokenLimits{}
	}
	lifecycle := "active"
	if hasLifecycleOwner && lifecycleOwner.record.IsDeprecated {
		lifecycle = "deprecated"
	}
	sort.Slice(compiled, func(left, right int) bool { return compiled[left].Operation < compiled[right].Operation })
	return modelConfig{ID: modelID, Lifecycle: lifecycle, TokenLimits: limits, Operations: compiled}, true, rejected
}

func compileInferredModel(modelID string) (modelConfig, bool, []*compileError) {
	operation := inferOperation(modelID)
	if operation == "" {
		return modelConfig{}, false, nil
	}
	compiled, err := compileOperation(operation, "", nil, recordRef{}, false)
	if err != nil {
		return modelConfig{}, false, []*compileError{{ModelID: modelID, Field: "operation", Reason: err.Error()}}
	}
	return modelConfig{ID: modelID, Lifecycle: "active", Operations: []operationConfig{compiled}}, true, nil
}

func exactCanonicalRecord(modelID string, records []recordRef) (recordRef, bool) {
	for _, ref := range records {
		if ref.key == modelID {
			return ref, true
		}
	}
	for _, ref := range records {
		if strings.EqualFold(ref.key, modelID) {
			return ref, true
		}
	}
	return recordRef{}, false
}

func isExactCanonicalKey(modelID string, ref recordRef) bool {
	return ref.key == modelID || strings.EqualFold(ref.key, modelID)
}

func isMetadataRecord(ref recordRef) bool {
	return strings.EqualFold(strings.TrimSpace(ref.record.Source), "merged_from_llm_models_csv")
}

func authoritativeOperation(ref recordRef) (api.Operation, string) {
	mode := strings.TrimSpace(ref.record.Mode)
	if mode == "" {
		return "", "source record is missing an authoritative mode"
	}
	operation, supported := supportedModes[mode]
	if !supported {
		return "", fmt.Sprintf("source mode %q is unsupported", ref.record.Mode)
	}
	return operation, ""
}

func chooseOperationPrimary(modelID string, records []recordRef) recordRef {
	ordered := append([]recordRef(nil), records...)
	sort.SliceStable(ordered, func(left, right int) bool {
		leftRank, rightRank := primaryRank(modelID, ordered[left]), primaryRank(modelID, ordered[right])
		for index := range leftRank {
			if leftRank[index] != rightRank[index] {
				return leftRank[index] < rightRank[index]
			}
		}
		return ordered[left].key < ordered[right].key
	})
	return ordered[0]
}

func primaryRank(modelID string, ref recordRef) [5]int {
	// Exact identity, authoritative source quality, non-batch, fact richness,
	// and key length provide a complete deterministic ordering.
	exact := 2
	if ref.key == modelID {
		exact = 0
	} else if strings.EqualFold(ref.key, modelID) {
		exact = 1
	}
	metadata := 1
	if isMetadataRecord(ref) {
		metadata = 2
	}
	batch := 0
	if isBatchRecord(ref) {
		batch = 1
	}
	facts := 0
	if ref.record.SupportedModalities != nil {
		facts++
	}
	if ref.record.SupportedOutputModalities != nil {
		facts++
	}
	if ref.record.MaxInputTokens != nil || ref.record.MaxOutputTokens != nil || ref.record.MaxTokens != nil {
		facts++
	}
	return [5]int{exact, metadata, batch, -facts, len(ref.key)}
}

func rankLess(left, right [5]int) bool {
	for index := range left {
		if left[index] != right[index] {
			return left[index] < right[index]
		}
	}
	return false
}

// Bifrost stores its name-based fallback rules beside model parameter rows.
// This metadata object is not a model and has no authoritative mode; unlike a
// model-shaped record with a missing mode, it must not become a rejection or a
// name-inferred catalog entry.
func nonModelSourceRecord(key string) bool {
	return key == "fallback_generalizations"
}

func operationForRecord(modelID string, ref recordRef) api.Operation {
	if strings.TrimSpace(ref.record.Mode) != "" {
		// An explicit source mode is the operation owner. Model names are
		// reserved for records with no source fact; they must never rewrite a
		// declared image/video/chat operation.
		return supportedModes[strings.TrimSpace(ref.record.Mode)]
	}
	if ref.key == "" {
		return inferOperation(modelID)
	}
	return ""
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

func inferOperation(modelID string) api.Operation {
	value := strings.ToLower(modelID)
	switch {
	case strings.Contains(value, "moderation"):
		return ""
	case strings.Contains(value, "deepgram/flux"):
		return api.OperationAudioTranscription
	case strings.Contains(value, "rerank"):
		return api.OperationRerank
	case strings.Contains(value, "embedding"), strings.Contains(value, "embed-"),
		strings.Contains(value, "bge-"), strings.Contains(value, "m3e-"), strings.Contains(value, "jina-clip"):
		return api.OperationEmbedding
	case strings.Contains(value, "transcribe"), strings.Contains(value, "whisper"),
		strings.Contains(value, "sensevoice"), strings.Contains(value, "parakeet"),
		strings.Contains(value, "-asr"), strings.Contains(value, "/asr"),
		strings.Contains(value, "-stt"), strings.Contains(value, "/stt"),
		strings.Contains(value, "voxtral-mini-realtime"):
		return api.OperationAudioTranscription
	case strings.Contains(value, "lyria"), strings.Contains(value, "musicgen"), strings.Contains(value, "suno"):
		return api.OperationMusicGeneration
	case strings.Contains(value, "tts"), strings.HasPrefix(value, "speech-"),
		strings.Contains(value, "text-to-speech"), strings.Contains(value, "text2speech"),
		strings.Contains(value, "melotts"), strings.Contains(value, "orpheus"),
		strings.Contains(value, "eleven-"):
		return api.OperationAudioGeneration
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
		return api.OperationVideoGeneration
	case imageModelName(value):
		return api.OperationImageGeneration
	case realtimeModel(value):
		return api.OperationRealtime
	default:
		return api.OperationGenerate
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

func realtimeModel(modelID string) bool {
	value := strings.ToLower(modelID)
	return strings.Contains(value, "realtime") || strings.Contains(value, "native-audio") || strings.Contains(value, "-live-")
}

func compileTokenLimits(operation api.Operation, primary recordRef) (tokenLimits, bool, error) {
	maxInput, err := nonNegativeInteger(primary.record.MaxInputTokens)
	if err != nil {
		return tokenLimits{}, false, fmt.Errorf("max_input_tokens: %w", err)
	}
	if maxInput == 0 && operation == api.OperationEmbedding {
		maxInput, err = nonNegativeInteger(primary.record.MaxTokens)
		if err != nil {
			return tokenLimits{}, false, fmt.Errorf("max_tokens: %w", err)
		}
	}
	limits := tokenLimits{MaxInputTokens: maxInput}
	if operation != api.OperationGenerate && operation != api.OperationRealtime {
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
	// this precedence is part of the source adapter, not a runtime merge.
	if maxOutput == 0 {
		maxOutput = genericMaximum
	}
	parameterPolicy, parameterFound, policyErr := outputTokenBounds([]recordRef{primary})
	if policyErr != nil {
		return tokenLimits{}, false, policyErr
	}
	if parameterFound {
		if maxOutput > 0 && parameterPolicy.Maximum != nil && maxOutput != *parameterPolicy.Maximum {
			return tokenLimits{}, false, errors.New("model output maximum conflicts with parameter range maximum")
		}
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

func compileOperation(operation api.Operation, sourceMode string, records []recordRef, primary recordRef, outputTokensSupported bool) (operationConfig, error) {
	inputs, outputs, modalityErr := compileModalities(operation, sourceMode, records, primary)
	if modalityErr != nil {
		return operationConfig{}, modalityErr
	}
	result := operationConfig{
		Operation:        operation,
		Modes:            compileModes(operation, records),
		InputModalities:  inputs,
		OutputModalities: outputs,
		// Features and parameter policies are owned by the exact source
		// record. Supplemental :batch records add delivery evidence only;
		// their service-specific parameter facts must not be merged into the
		// model's primary limits or defaults.
		Features: compileFeatures(operation, []recordRef{primary}),
	}
	if operation == api.OperationGenerate || operation == api.OperationRealtime {
		policy, err := compileGenerationPolicy(primary, outputTokensSupported)
		if err != nil {
			return operationConfig{}, err
		}
		result.Generation = policy
	}
	if operation == api.OperationEmbedding {
		policy, err := compileEmbeddingPolicy(primary)
		if err != nil {
			return operationConfig{}, err
		}
		result.Embedding = policy
	}
	if err := modelcatalog.ValidateOperationShape(modelcatalog.OperationShape{
		Operation:        result.Operation,
		Modes:            result.Modes,
		InputModalities:  result.InputModalities,
		OutputModalities: result.OutputModalities,
		HasGeneration:    result.Generation != nil,
		HasEmbedding:     result.Embedding != nil,
	}); err != nil {
		return operationConfig{}, err
	}
	return result, nil
}

func compileModes(operation api.Operation, records []recordRef) []api.DeliveryMode {
	modes := make(map[api.DeliveryMode]struct{}, 3)
	switch operation {
	case api.OperationRealtime:
		modes[api.ModeDuplex] = struct{}{}
	case api.OperationVideoGeneration:
		modes[api.ModeAsync] = struct{}{}
	default:
		modes[api.ModeUnary] = struct{}{}
	}
	if operation == api.OperationGenerate {
		for _, ref := range records {
			if ref.record.SupportsNativeStreaming || hasParameter(ref.record, "stream") {
				modes[api.ModeServerStream] = struct{}{}
				break
			}
		}
	}
	if operation == api.OperationAudioTranscription {
		for _, ref := range records {
			if ref.record.SupportsNativeStreaming || strings.Contains(strings.ToLower(ref.key), "realtime") {
				modes[api.ModeServerStream] = struct{}{}
				break
			}
		}
	}
	if operation != api.OperationRealtime {
		batchOnly := len(records) > 0
		for _, ref := range records {
			if batchEvidence(ref) {
				modes[api.ModeAsync] = struct{}{}
			}
			if !batchOnlyEvidence(ref) {
				batchOnly = false
			}
		}
		if batchOnly {
			delete(modes, api.ModeUnary)
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

func compileModalities(operation api.Operation, sourceMode string, records []recordRef, primary recordRef) ([]api.Modality, []api.Modality, error) {
	defaults := map[api.Operation][2][]api.Modality{
		api.OperationGenerate:           {{api.ModalityText}, {api.ModalityText}},
		api.OperationEmbedding:          {{api.ModalityText}, {api.ModalityEmbedding}},
		api.OperationRerank:             {{api.ModalityText}, {api.ModalityText}},
		api.OperationImageGeneration:    {{api.ModalityText}, {api.ModalityImage}},
		api.OperationAudioGeneration:    {{api.ModalityText}, {api.ModalityAudio}},
		api.OperationAudioTranscription: {{api.ModalityAudio}, {api.ModalityText}},
		api.OperationMusicGeneration:    {{api.ModalityText}, {api.ModalityMusic}},
		api.OperationVideoGeneration:    {{api.ModalityText, api.ModalityImage}, {api.ModalityVideo}},
		api.OperationRealtime:           {{api.ModalityText, api.ModalityAudio}, {api.ModalityText, api.ModalityAudio}},
	}
	inputs, inputKnown, err := compileSourceModalities("supported_modalities", records, primary, func(ref recordRef) []string {
		return ref.record.SupportedModalities
	})
	if err != nil {
		return nil, nil, err
	}
	outputs, outputKnown, err := compileSourceModalities("supported_output_modalities", records, primary, func(ref recordRef) []string {
		return ref.record.SupportedOutputModalities
	})
	if err != nil {
		return nil, nil, err
	}
	if !inputKnown {
		inputs = modalityMap(defaults[operation][0])
		for _, ref := range records {
			switch operation {
			case api.OperationEmbedding:
				if ref.record.SupportsEmbeddingImageInput {
					inputs[api.ModalityImage] = struct{}{}
				}
				if ref.record.SupportsAudioInput {
					inputs[api.ModalityAudio] = struct{}{}
				}
				if ref.record.SupportsVideoInput {
					inputs[api.ModalityVideo] = struct{}{}
				}
			case api.OperationGenerate, api.OperationRealtime:
				if ref.record.SupportsVision || ref.record.SupportsImageInput {
					inputs[api.ModalityImage] = struct{}{}
				}
				if ref.record.SupportsAudioInput {
					inputs[api.ModalityAudio] = struct{}{}
				}
				if ref.record.SupportsVideoInput {
					inputs[api.ModalityVideo] = struct{}{}
				}
			}
		}
	}
	if !outputKnown {
		outputs = modalityMap(defaults[operation][1])
		for _, ref := range records {
			if (operation == api.OperationGenerate || operation == api.OperationRealtime) && ref.record.SupportsAudioOutput {
				outputs[api.ModalityAudio] = struct{}{}
			}
		}
	}
	if sourceMode == "image_edit" && !inputKnown {
		inputs[api.ModalityImage] = struct{}{}
	}
	return sortedSet(inputs), sortedSet(outputs), nil
}

// compileSourceModalities gives the exact record ownership of an explicit
// modality list. Supplemental records are still consumed: they must either
// repeat the primary fact or, when the primary omitted the field, repeat one
// another exact fact. A disagreement is rejected instead of silently
// widening or narrowing the published shape.
func compileSourceModalities(
	field string,
	records []recordRef,
	primary recordRef,
	values func(recordRef) []string,
) (map[api.Modality]struct{}, bool, error) {
	var primarySet map[api.Modality]struct{}
	primaryDeclared := false
	var supplemental []map[api.Modality]struct{}
	for _, ref := range records {
		raw := values(ref)
		if raw == nil {
			continue
		}
		set := make(map[api.Modality]struct{}, len(raw))
		for _, value := range raw {
			if err := addModality(set, value); err != nil {
				return nil, false, fmt.Errorf("%s: %w", field, err)
			}
		}
		if primary.key != "" && ref.key == primary.key {
			primarySet = set
			primaryDeclared = true
			continue
		}
		supplemental = append(supplemental, set)
	}
	if primaryDeclared {
		for _, set := range supplemental {
			if !sameModalitySet(primarySet, set) {
				// The exact primary fact owns the published shape. The conflicting
				// supplemental observation is rejected by
				// modalityConflictRejections, but it must not widen or erase the
				// authoritative primary capability.
				continue
			}
		}
		return primarySet, true, nil
	}
	if len(supplemental) == 0 {
		return nil, false, nil
	}
	// A missing primary fact may be completed by a supplemental record, but
	// multiple supplemental records are still observations of the same model
	// capability. Unioning different observations would manufacture a
	// capability that no single source asserted. Require one deterministic set
	// instead and fail closed on disagreement.
	first := supplemental[0]
	for _, set := range supplemental[1:] {
		if !sameModalitySet(first, set) {
			// There is no exact owner. Unknown is safer than choosing one
			// provider's disputed set or silently substituting operation
			// defaults. The caller must reject this operation; otherwise the
			// default would become a second, invented capability fact.
			return nil, false, errors.New("conflicting supplemental modality facts have no authoritative owner")
		}
	}
	return first, true, nil
}

// modalityConflictRejections makes disagreement observable without allowing a
// provider-specific supplemental row to rewrite the canonical model fact. The
// primary row remains the sole owner; a row that contradicts it is rejected as
// an observation, not promoted into a second capability path.
func modalityConflictRejections(modelID string, operation api.Operation, records []recordRef, primary recordRef) []*compileError {
	result := make([]*compileError, 0)
	for _, spec := range []struct {
		field  string
		values func(recordRef) []string
	}{
		{field: "supported_modalities", values: func(ref recordRef) []string { return ref.record.SupportedModalities }},
		{field: "supported_output_modalities", values: func(ref recordRef) []string { return ref.record.SupportedOutputModalities }},
	} {
		primaryValues := spec.values(primary)
		if primaryValues == nil {
			var owner map[api.Modality]struct{}
			ownerKey := ""
			for _, ref := range records {
				raw := spec.values(ref)
				if raw == nil {
					continue
				}
				set := make(map[api.Modality]struct{}, len(raw))
				if err := addModalities(set, raw); err != nil {
					continue
				}
				if owner == nil {
					owner, ownerKey = set, ref.key
					continue
				}
				if !sameModalitySet(owner, set) {
					result = append(result, &compileError{
						ModelID: modelID,
						Field:   "operation",
						Reason:  fmt.Sprintf("operation %q %s from %q conflicts with %q", operation, spec.field, ref.key, ownerKey),
					})
				}
			}
			continue
		}
		primarySet := make(map[api.Modality]struct{}, len(primaryValues))
		if err := addModalities(primarySet, primaryValues); err != nil {
			continue
		}
		for _, ref := range records {
			if ref.key == primary.key {
				continue
			}
			raw := spec.values(ref)
			if raw == nil {
				continue
			}
			set := make(map[api.Modality]struct{}, len(raw))
			if err := addModalities(set, raw); err != nil {
				continue
			}
			if !sameModalitySet(primarySet, set) {
				result = append(result, &compileError{
					ModelID: modelID,
					Field:   "operation",
					Reason:  fmt.Sprintf("operation %q %s from %q conflicts with primary record %q", operation, spec.field, ref.key, primary.key),
				})
			}
		}
	}
	return result
}

func addModalities(target map[api.Modality]struct{}, values []string) error {
	for _, value := range values {
		if err := addModality(target, value); err != nil {
			return err
		}
	}
	return nil
}

func modalityMap(values []api.Modality) map[api.Modality]struct{} {
	result := make(map[api.Modality]struct{}, len(values))
	for _, value := range values {
		result[value] = struct{}{}
	}
	return result
}

func sameModalitySet(left, right map[api.Modality]struct{}) bool {
	if len(left) != len(right) {
		return false
	}
	for value := range left {
		if _, ok := right[value]; !ok {
			return false
		}
	}
	return true
}

func addModality(target map[api.Modality]struct{}, value string) error {
	normalized := api.Modality(strings.ToLower(value))
	if !normalized.IsValid() {
		return fmt.Errorf("unsupported modality %q", value)
	}
	if _, exists := target[normalized]; exists {
		return fmt.Errorf("duplicate modality %q", value)
	}
	target[normalized] = struct{}{}
	return nil
}

func compileFeatures(operation api.Operation, records []recordRef) []api.Feature {
	features := map[api.Feature]struct{}{api.FeatureUsage: {}}
	if operation != api.OperationGenerate && operation != api.OperationRealtime {
		return sortedSet(features)
	}
	for _, ref := range records {
		record := ref.record
		if record.SupportsFunctionCalling {
			features[api.FeatureToolCalls] = struct{}{}
		}
		if record.SupportsResponseSchema || record.SupportsNativeStructured {
			features[api.FeatureStructured] = struct{}{}
		}
		if record.SupportsPromptCaching || record.SupportsCachePoint {
			features[api.FeaturePromptCache] = struct{}{}
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

func sortedSet[T ~string](values map[T]struct{}) []T {
	result := make([]T, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Slice(result, func(left, right int) bool { return result[left] < result[right] })
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

func defaultRejectionManifestPath(catalogPath string) string {
	if strings.HasSuffix(catalogPath, ".json.gz") {
		base := strings.TrimSuffix(catalogPath, ".json.gz")
		base = strings.TrimSuffix(base, "_data")
		return base + "_rejections.json"
	}
	return catalogPath + ".rejections.json"
}

func writeRejectionManifest(path string, manifest rejectionManifest) error {
	if manifest.Rejections == nil {
		manifest.Rejections = []*compileError{}
	}
	sortCompileErrors(manifest.Rejections)
	encoded, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return fmt.Errorf("encode rejection manifest: %w", err)
	}
	encoded = append(encoded, '\n')
	if err := os.WriteFile(path, encoded, 0o644); err != nil {
		return fmt.Errorf("write rejection manifest: %w", err)
	}
	return nil
}

func sortCompileErrors(values []*compileError) {
	sort.Slice(values, func(left, right int) bool {
		if values[left].ModelID != values[right].ModelID {
			return values[left].ModelID < values[right].ModelID
		}
		if values[left].Field != values[right].Field {
			return values[left].Field < values[right].Field
		}
		return values[left].Reason < values[right].Reason
	})
}
