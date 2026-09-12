package main

import (
	"fmt"
	"strings"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	modelcatalog "github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
)

func compileOperation(operation api.Operation, records []recordRef, outputTokensSupported bool) (modelcatalog.CapabilityConfig, error) {
	inputs, outputs, modalityErr := compileModalities(operation, sourceModeForRecords(records), records)
	if modalityErr != nil {
		return modelcatalog.CapabilityConfig{}, modalityErr
	}
	features, featureErr := compileFeatures(operation, records)
	if featureErr != nil {
		return modelcatalog.CapabilityConfig{}, featureErr
	}
	result := modelcatalog.CapabilityConfig{
		Operation:        operation,
		InputModalities:  inputs,
		OutputModalities: outputs,
		Features:         features,
	}
	if operation == api.OperationGenerate || operation == api.OperationRealtime {
		policy, err := compileGenerationPolicy(records, outputTokensSupported)
		if err != nil {
			return modelcatalog.CapabilityConfig{}, err
		}
		result.Generation = policy
	}
	if operation == api.OperationEmbedding {
		policy, err := compileEmbeddingPolicy(records)
		if err != nil {
			return modelcatalog.CapabilityConfig{}, err
		}
		result.Embedding = policy
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

func compileModalities(operation api.Operation, sourceMode string, records []recordRef) ([]api.Modality, []api.Modality, error) {
	defaultInputs, defaultOutputs, knownOperation := modelcatalog.DefaultCapabilityModalities(operation)
	if !knownOperation {
		return nil, nil, fmt.Errorf("unsupported operation %q", operation)
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
		inputs = modalityMap(defaultInputs)
		var inputFacts []modalityFact
		switch operation {
		case api.OperationEmbedding:
			inputFacts = []modalityFact{
				{modality: api.ModalityImage, name: "embedding image input", fields: []booleanField{
					{name: "supports_embedding_image_input", value: func(record sourceRecord) *bool { return record.SupportsEmbeddingImageInput }},
				}},
				{modality: api.ModalityAudio, name: "audio input", fields: []booleanField{
					{name: "supports_audio_input", value: func(record sourceRecord) *bool { return record.SupportsAudioInput }},
				}},
				{modality: api.ModalityVideo, name: "video input", fields: []booleanField{
					{name: "supports_video_input", value: func(record sourceRecord) *bool { return record.SupportsVideoInput }},
				}},
			}
		case api.OperationGenerate, api.OperationRealtime:
			inputFacts = []modalityFact{
				{modality: api.ModalityImage, name: "image input", fields: []booleanField{
					{name: "supports_vision", value: func(record sourceRecord) *bool { return record.SupportsVision }},
					{name: "supports_image_input", value: func(record sourceRecord) *bool { return record.SupportsImageInput }},
				}},
				{modality: api.ModalityAudio, name: "audio input", fields: []booleanField{
					{name: "supports_audio_input", value: func(record sourceRecord) *bool { return record.SupportsAudioInput }},
				}},
				{modality: api.ModalityVideo, name: "video input", fields: []booleanField{
					{name: "supports_video_input", value: func(record sourceRecord) *bool { return record.SupportsVideoInput }},
				}},
			}
		}
		for _, fact := range inputFacts {
			enabled, factErr := compileAnyBooleanFact(fact.name, records, fact.fields)
			if factErr != nil {
				return nil, nil, factErr
			}
			if enabled {
				inputs[fact.modality] = struct{}{}
			}
		}
	}
	if !outputKnown {
		outputs = modalityMap(defaultOutputs)
		if operation == api.OperationGenerate || operation == api.OperationRealtime {
			audio, factErr := compileAnyBooleanFact("audio output", records, []booleanField{
				{name: "supports_audio_output", value: func(record sourceRecord) *bool { return record.SupportsAudioOutput }},
			})
			if factErr != nil {
				return nil, nil, factErr
			}
			if audio {
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
// must be identical. A disagreement invalidates the BaseModel rather
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

type modalityFact struct {
	modality api.Modality
	name     string
	fields   []booleanField
}

// compileAnyBooleanFact unions distinct positive evidence fields only after
// each individual source field has proven internally consistent.
func compileAnyBooleanFact(name string, records []recordRef, fields []booleanField) (bool, error) {
	enabled := false
	for _, field := range fields {
		value, err := compileBooleanField(field, records)
		if err != nil {
			return false, fmt.Errorf("%s: %w", name, err)
		}
		enabled = enabled || value != nil && *value
	}
	return enabled, nil
}

func compileFeatures(operation api.Operation, records []recordRef) ([]api.Feature, error) {
	features := make(map[api.Feature]struct{}, 3)
	if operation != api.OperationGenerate && operation != api.OperationRealtime {
		return nil, nil
	}
	facts := []struct {
		feature api.Feature
		name    string
		fields  []booleanField
	}{
		{feature: api.FeatureToolCalls, name: "tool calling", fields: []booleanField{
			{name: "supports_function_calling", value: func(record sourceRecord) *bool { return record.SupportsFunctionCalling }},
		}},
		{feature: api.FeatureStructured, name: "structured output compatibility", fields: []booleanField{
			{name: "supports_response_schema", value: func(record sourceRecord) *bool { return record.SupportsResponseSchema }},
			{name: "supports_native_structured_output", value: func(record sourceRecord) *bool { return record.SupportsNativeStructured }},
		}},
	}
	for _, fact := range facts {
		enabled, err := compileAnyBooleanFact(fact.name, records, fact.fields)
		if err != nil {
			return nil, err
		}
		if enabled {
			features[fact.feature] = struct{}{}
		}
	}
	return sortedSet(features), nil
}
