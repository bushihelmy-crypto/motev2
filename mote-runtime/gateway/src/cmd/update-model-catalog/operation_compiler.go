package main

import (
	"fmt"
	"strings"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	modelcatalog "github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
)

func compileOperation(operation api.Operation, records []recordRef, outputTokensSupported bool) (operationConfig, error) {
	inputs, outputs, modalityErr := compileModalities(operation, sourceModeForRecords(records), records)
	if modalityErr != nil {
		return operationConfig{}, modalityErr
	}
	result := operationConfig{
		Operation:        operation,
		Modes:            compileModes(operation, records),
		InputModalities:  inputs,
		OutputModalities: outputs,
		Features:         compileFeatures(operation, records),
	}
	if operation == api.OperationGenerate || operation == api.OperationRealtime {
		policy, err := compileGenerationPolicy(records, outputTokensSupported)
		if err != nil {
			return operationConfig{}, err
		}
		result.Generation = policy
	}
	if operation == api.OperationEmbedding {
		policy, err := compileEmbeddingPolicy(records)
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

func sourceModeForRecords(records []recordRef) string {
	for _, ref := range records {
		if strings.EqualFold(strings.TrimSpace(ref.record.Mode), "image_edit") {
			return "image_edit"
		}
	}
	return ""
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

func compileModalities(operation api.Operation, sourceMode string, records []recordRef) ([]api.Modality, []api.Modality, error) {
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
	inputs, inputKnown, err := compileSourceModalities("supported_modalities", records, func(ref recordRef) []string {
		return ref.record.SupportedModalities
	})
	if err != nil {
		return nil, nil, err
	}
	outputs, outputKnown, err := compileSourceModalities("supported_output_modalities", records, func(ref recordRef) []string {
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

// compileSourceModalities merges equal-weight source observations. An explicit
// list is a model fact, not a provider preference: all non-empty declarations
// must be identical. A disagreement invalidates the canonical model rather
// than allowing one source row to widen or narrow it.
func compileSourceModalities(
	field string,
	records []recordRef,
	values func(recordRef) []string,
) (map[api.Modality]struct{}, bool, error) {
	var owner map[api.Modality]struct{}
	ownerKey := ""
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
		if owner == nil {
			owner, ownerKey = set, ref.key
			continue
		}
		if !sameModalitySet(owner, set) {
			return nil, false, fmt.Errorf("%s from %q conflicts with %q", field, ref.key, ownerKey)
		}
	}
	return owner, owner != nil, nil
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
