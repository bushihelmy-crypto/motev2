package main

import (
	"fmt"
	"sort"
	"strings"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	modelcatalog "github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
)

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

// compileModels groups rows by their authoritative bare BaseModel. Source keys
// (provider/service names) never create identities, and rows without a valid
// BaseModel are discarded.
func compileModels(records []recordRef) ([]modelcatalog.Config, []*compileError) {
	groups := make(map[string][]recordRef)
	rejected := make([]*compileError, 0)
	for _, ref := range records {
		if ref.key == "" || nonModelSourceRecord(ref.key) || isMetadataRecord(ref) || isBatchRecord(ref) {
			continue
		}
		modelID := ref.record.BaseModel
		if err := modelcatalog.ValidateBaseModel(modelID); err != nil {
			rejected = append(rejected, &compileError{ModelID: ref.key, Field: "base_model", Reason: err.Error()})
			continue
		}
		if syntheticModelID(modelID) {
			continue
		}
		groups[modelID] = append(groups[modelID], ref)
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
	models := make([]modelcatalog.Config, 0, len(ids))
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

// compileModelGroup is the only path that turns one authoritative BaseModel
// into a catalog definition. Every source row in the group has equal weight;
// no provider or service row can become a hidden primary. Facts that agree are
// deduplicated, facts that complement one another are completed, and a
// contradictory fact invalidates the whole model identity.
func compileModelGroup(modelID string, matches []recordRef) (modelcatalog.Config, bool, []*compileError) {
	if len(matches) == 0 {
		return modelcatalog.Config{}, false, nil
	}

	sortRecords(matches)
	records := make([]recordRef, 0, len(matches))
	rejected := make([]*compileError, 0)
	hasMalformedSource := false
	var operation api.Operation
	operationOwner := ""
	for _, ref := range matches {
		// Metadata rows are not model evidence. They are filtered before grouping
		// and remain ignored here as a defensive boundary.
		if isMetadataRecord(ref) {
			continue
		}
		baseModel := ref.record.BaseModel
		if baseErr := modelcatalog.ValidateBaseModel(baseModel); baseErr != nil {
			hasMalformedSource = true
			rejected = append(rejected, &compileError{ModelID: modelID, Field: "base_model", Reason: fmt.Sprintf("source record %q: %s", ref.key, baseErr)})
			continue
		}
		if baseModel != modelID {
			hasMalformedSource = true
			rejected = append(rejected, &compileError{ModelID: modelID, Field: "base_model", Reason: fmt.Sprintf("source record %q declares %q, expected %q", ref.key, baseModel, modelID)})
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
			// A single BaseModel may only have one semantic operation. If source
			// records disagree, the identity is ambiguous and is removed as a whole.
			rejected = append(rejected, &compileError{
				ModelID: modelID,
				Field:   "operation",
				Reason:  fmt.Sprintf("source records %q and %q declare conflicting operations (%s and %s)", operationOwner, ref.key, operation, candidate),
			})
			return modelcatalog.Config{}, false, rejected
		}
		if operationOwner == "" {
			operation, operationOwner = candidate, ref.key
		}
		records = append(records, ref)
	}

	if len(records) == 0 {
		if hasMalformedSource {
			// A malformed source row is evidence about this identity. Do not hide
			// it by publishing a partially inferred definition.
			return modelcatalog.Config{}, false, rejected
		}
		return modelcatalog.Config{}, false, rejected
	}

	if hasMalformedSource {
		// Once a BaseModel has an invalid authoritative row, keeping a
		// different row would make the same public name depend on which source
		// happened to be selected. Treat it like any other identity conflict.
		return modelcatalog.Config{}, false, rejected
	}

	limits, outputTokensSupported, err := compileTokenLimits(operation, records)
	if err != nil {
		rejected = append(rejected, &compileError{ModelID: modelID, Field: "token_limits", Reason: fmt.Sprintf("operation %q: %s", operation, err)})
		return modelcatalog.Config{}, false, rejected
	}
	compiled, err := compileOperation(operation, records, outputTokensSupported)
	if err != nil {
		rejected = append(rejected, &compileError{ModelID: modelID, Field: "operation", Reason: fmt.Sprintf("operation %q: %s", operation, err)})
		return modelcatalog.Config{}, false, rejected
	}
	lifecycle, err := compileLifecycle(records)
	if err != nil {
		rejected = append(rejected, &compileError{ModelID: modelID, Field: "lifecycle", Reason: err.Error()})
		return modelcatalog.Config{}, false, rejected
	}
	model := modelcatalog.Config{
		BaseModel:   modelID,
		Lifecycle:   lifecycle,
		TokenLimits: limits,
		Capability:  compiled,
	}
	if err := modelcatalog.ValidateConfig(model); err != nil {
		rejected = append(rejected, &compileError{ModelID: modelID, Field: "capability", Reason: err.Error()})
		return modelcatalog.Config{}, false, rejected
	}
	return model, true, rejected
}

func compileLifecycle(records []recordRef) (modelcatalog.Lifecycle, error) {
	deprecated, err := compileBooleanField(booleanField{
		name:  "is_deprecated",
		value: func(record sourceRecord) *bool { return record.IsDeprecated },
	}, records)
	if err != nil {
		return "", err
	}
	if deprecated != nil && *deprecated {
		return modelcatalog.LifecycleDeprecated, nil
	}
	return modelcatalog.LifecycleActive, nil
}

type booleanField struct {
	name  string
	value func(sourceRecord) *bool
}

// compileBooleanField keeps missing distinct from false. Positive observations
// may complete an unknown model fact, while explicit true/false disagreement
// invalidates the BaseModel instead of being ORed across source rows.
func compileBooleanField(field booleanField, records []recordRef) (*bool, error) {
	var owner *bool
	ownerKey := ""
	for _, ref := range records {
		candidate := field.value(ref.record)
		if candidate == nil {
			continue
		}
		if owner != nil && *owner != *candidate {
			return nil, fmt.Errorf("%s from %q conflicts with %q", field.name, ref.key, ownerKey)
		}
		if owner == nil {
			value := *candidate
			owner, ownerKey = &value, ref.key
		}
	}
	return owner, nil
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
// name-inferred catalog entry. The compiler has no fallback identity path.
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
