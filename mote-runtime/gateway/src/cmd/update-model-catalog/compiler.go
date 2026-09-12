package main

import (
	"fmt"
	"sort"
	"strings"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

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

// compileModels groups provider-qualified rows by one canonical model ID.
// chat/completion/responses are normalized to generate; a canonical ID that is
// claimed by more than one semantic operation is rejected as ambiguous.
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
// into a catalog definition. Every source row in the group has equal weight:
// prefixes have already been removed, so no row can become a hidden primary.
// Facts that agree are deduplicated, facts that complement one another are
// completed, and a contradictory fact invalidates the whole model identity.
func compileModelGroup(modelID string, matches []recordRef) (modelConfig, bool, []*compileError) {
	if len(matches) == 0 {
		return compileInferredModel(modelID)
	}

	sortRecords(matches)
	records := make([]recordRef, 0, len(matches))
	rejected := make([]*compileError, 0)
	hasMalformedSource := false
	var operation api.Operation
	operationOwner := ""
	for _, ref := range matches {
		// Rows produced by the pricing CSV merger carry a mode for display, not
		// an authoritative model operation.  They may identify a candidate
		// model, but must never override a real capability row.
		if isMetadataRecord(ref) {
			continue
		}
		candidate, reason := authoritativeOperation(ref)
		if reason != "" {
			hasMalformedSource = true
			field := "operation"
			if strings.TrimSpace(ref.record.Mode) == "" {
				field = "mode"
			}
			rejected = append(rejected, &compileError{ModelID: modelID, Field: field, Reason: fmt.Sprintf("source record %q: %s", ref.key, reason)})
			continue
		}
		if operationOwner != "" && operation != candidate {
			// Prefix removal intentionally makes one leaf one identity. If the
			// normalized identity is claimed by different semantic operations,
			// there is no safe way to know whether this is one model or two
			// unrelated models with the same leaf. Drop the whole identity.
			rejected = append(rejected, &compileError{
				ModelID: modelID,
				Field:   "operation",
				Reason:  fmt.Sprintf("source records %q and %q declare conflicting operations (%s and %s)", operationOwner, ref.key, operation, candidate),
			})
			return modelConfig{}, false, rejected
		}
		if operationOwner == "" {
			operation, operationOwner = candidate, ref.key
		}
		records = append(records, ref)
	}

	if len(records) == 0 {
		if hasMalformedSource {
			// A malformed source row is evidence about this identity. Do not hide
			// it by publishing a name-inferred operation.
			return modelConfig{}, false, rejected
		}
		// A model represented only by metadata rows still gets one operation,
		// but the operation is inferred from its canonical ID because no source
		// record claimed an authoritative fact.
		inferred, present, inferredRejected := compileInferredModel(modelID)
		return inferred, present, append(rejected, inferredRejected...)
	}

	if hasMalformedSource {
		// Once a canonical identity has an invalid authoritative row, keeping a
		// different row would make the same public name depend on which source
		// happened to be selected. Treat it like any other identity conflict.
		return modelConfig{}, false, rejected
	}

	limits, outputTokensSupported, err := compileTokenLimits(operation, records)
	if err != nil {
		rejected = append(rejected, &compileError{ModelID: modelID, Field: "token_limits", Reason: fmt.Sprintf("operation %q: %s", operation, err)})
		return modelConfig{}, false, rejected
	}
	compiled, err := compileOperation(operation, records, outputTokensSupported)
	if err != nil {
		rejected = append(rejected, &compileError{ModelID: modelID, Field: "operation", Reason: fmt.Sprintf("operation %q: %s", operation, err)})
		return modelConfig{}, false, rejected
	}
	lifecycleDeprecated := false
	for _, ref := range records {
		lifecycleDeprecated = lifecycleDeprecated || ref.record.IsDeprecated
	}
	lifecycle := "active"
	if lifecycleDeprecated {
		lifecycle = "deprecated"
	}
	return modelConfig{
		ID:          modelID,
		Lifecycle:   lifecycle,
		TokenLimits: limits,
		Operations:  []operationConfig{compiled},
	}, true, rejected
}

func compileInferredModel(modelID string) (modelConfig, bool, []*compileError) {
	operation := inferOperation(modelID)
	if operation == "" {
		return modelConfig{}, false, nil
	}
	compiled, err := compileOperation(operation, nil, false)
	if err != nil {
		return modelConfig{}, false, []*compileError{{ModelID: modelID, Field: "operation", Reason: err.Error()}}
	}
	return modelConfig{ID: modelID, Lifecycle: "active", Operations: []operationConfig{compiled}}, true, nil
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

// Bifrost stores its name-based fallback rules beside model parameter rows.
// This metadata object is not a model and has no authoritative mode; unlike a
// model-shaped record with a missing mode, it must not become a rejection or a
// name-inferred catalog entry.
func nonModelSourceRecord(key string) bool {
	return key == "fallback_generalizations"
}

func sortRecords(records []recordRef) {
	sort.Slice(records, func(left, right int) bool {
		leftKey, rightKey := strings.ToLower(records[left].key), strings.ToLower(records[right].key)
		if leftKey != rightKey {
			return leftKey < rightKey
		}
		return records[left].key < records[right].key
	})
}

func sortedSet[T ~string](values map[T]struct{}) []T {
	result := make([]T, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Slice(result, func(left, right int) bool { return result[left] < result[right] })
	return result
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
