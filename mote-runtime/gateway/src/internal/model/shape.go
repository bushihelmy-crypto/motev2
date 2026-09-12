package model

import (
	"fmt"
	"slices"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

// capabilityShape is the model-owned shape of one semantic operation.
// Delivery modes are deliberately absent: protocols and services own them.
type capabilityShape struct {
	Operation        api.Operation
	InputModalities  []api.Modality
	OutputModalities []api.Modality
	HasGeneration    bool
	HasEmbedding     bool
}

type operationShapePolicy struct {
	defaultInputs    []api.Modality
	defaultOutputs   []api.Modality
	allowedInputs    []api.Modality
	allowedOutputs   []api.Modality
	requiredOutput   api.Modality
	exactOutput      bool
	embedding        bool
	allowsGeneration bool
}

var operationShapePolicies = map[api.Operation]operationShapePolicy{
	api.OperationGenerate: {
		defaultInputs:    []api.Modality{api.ModalityText},
		defaultOutputs:   []api.Modality{api.ModalityText},
		allowedInputs:    []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs:   []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowsGeneration: true,
	},
	api.OperationEmbedding: {
		defaultInputs:  []api.Modality{api.ModalityText},
		defaultOutputs: []api.Modality{api.ModalityEmbedding},
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs: []api.Modality{api.ModalityEmbedding},
		requiredOutput: api.ModalityEmbedding,
		exactOutput:    true,
		embedding:      true,
	},
	api.OperationRerank: {
		defaultInputs:  []api.Modality{api.ModalityText},
		defaultOutputs: []api.Modality{api.ModalityText},
		allowedInputs:  []api.Modality{api.ModalityText},
		allowedOutputs: []api.Modality{api.ModalityText},
		requiredOutput: api.ModalityText,
	},
	api.OperationImageGeneration: {
		defaultInputs:  []api.Modality{api.ModalityText},
		defaultOutputs: []api.Modality{api.ModalityImage},
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs: []api.Modality{api.ModalityText, api.ModalityImage},
		requiredOutput: api.ModalityImage,
	},
	api.OperationAudioGeneration: {
		defaultInputs:  []api.Modality{api.ModalityText},
		defaultOutputs: []api.Modality{api.ModalityAudio},
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityAudio},
		allowedOutputs: []api.Modality{api.ModalityText, api.ModalityAudio},
		requiredOutput: api.ModalityAudio,
	},
	api.OperationAudioTranscription: {
		defaultInputs:  []api.Modality{api.ModalityAudio},
		defaultOutputs: []api.Modality{api.ModalityText},
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs: []api.Modality{api.ModalityText},
		requiredOutput: api.ModalityText,
	},
	api.OperationMusicGeneration: {
		defaultInputs:  []api.Modality{api.ModalityText},
		defaultOutputs: []api.Modality{api.ModalityMusic},
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityAudio},
		allowedOutputs: []api.Modality{api.ModalityText, api.ModalityAudio, api.ModalityMusic},
		requiredOutput: api.ModalityMusic,
	},
	api.OperationVideoGeneration: {
		defaultInputs:  []api.Modality{api.ModalityText, api.ModalityImage},
		defaultOutputs: []api.Modality{api.ModalityVideo},
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs: []api.Modality{api.ModalityText, api.ModalityVideo},
		requiredOutput: api.ModalityVideo,
	},
	api.OperationRealtime: {
		defaultInputs:    []api.Modality{api.ModalityText, api.ModalityAudio},
		defaultOutputs:   []api.Modality{api.ModalityText, api.ModalityAudio},
		allowedInputs:    []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs:   []api.Modality{api.ModalityText, api.ModalityAudio},
		allowsGeneration: true,
	},
}

// DefaultCapabilityModalities returns the conservative semantic defaults used
// only when a source omits an input or output modality list. The returned
// slices are copies so callers cannot mutate the shape owner's policy.
func DefaultCapabilityModalities(operation api.Operation) ([]api.Modality, []api.Modality, bool) {
	policy, ok := operationShapePolicies[operation]
	if !ok {
		return nil, nil, false
	}
	return append([]api.Modality(nil), policy.defaultInputs...), append([]api.Modality(nil), policy.defaultOutputs...), true
}

// validateCapabilityShape validates the one operation-shape rule.
// Explicit multimodal source facts remain valid when they are semantically
// compatible with the operation; profile-specific invocation limits belong to
// admission, not to this model-fact validator.
func validateCapabilityShape(shape capabilityShape) error {
	policy, ok := operationShapePolicies[shape.Operation]
	if !ok {
		return fmt.Errorf("unsupported operation %q", shape.Operation)
	}
	if len(shape.InputModalities) == 0 || len(shape.OutputModalities) == 0 {
		return fmt.Errorf("input/output modalities must not be empty")
	}
	if err := validateShapeSet("input modality", shape.InputModalities, policy.allowedInputs); err != nil {
		return err
	}
	if err := validateShapeSet("output modality", shape.OutputModalities, policy.allowedOutputs); err != nil {
		return err
	}
	if slices.Contains(shape.InputModalities, api.ModalityEmbedding) {
		return fmt.Errorf("embedding cannot be an operation input modality")
	}
	if shape.Operation == api.OperationRealtime {
		if !containsAny(shape.InputModalities, api.ModalityText, api.ModalityAudio) {
			return fmt.Errorf("realtime must accept text or audio input")
		}
		if !containsAny(shape.OutputModalities, api.ModalityText, api.ModalityAudio) {
			return fmt.Errorf("realtime must produce text or audio output")
		}
	}
	if policy.requiredOutput != "" && !slices.Contains(shape.OutputModalities, policy.requiredOutput) {
		return fmt.Errorf("%s must produce %s", shape.Operation, policy.requiredOutput)
	}
	if policy.exactOutput && !slices.Equal(shape.OutputModalities, []api.Modality{api.ModalityEmbedding}) {
		return fmt.Errorf("embedding must produce only the embedding modality")
	}
	if shape.HasEmbedding != policy.embedding {
		if policy.embedding {
			return fmt.Errorf("embedding must declare its embedding capability")
		}
		return fmt.Errorf("non-embedding operation must not declare embedding parameters")
	}
	if shape.HasGeneration && !policy.allowsGeneration {
		return fmt.Errorf("%s must not declare generation parameters", shape.Operation)
	}
	return nil
}

func validateShapeSet[T ~string](name string, values, allowed []T) error {
	seen := make(map[T]struct{}, len(values))
	for _, value := range values {
		if !slices.Contains(allowed, value) {
			return fmt.Errorf("%s %q is not allowed for this operation", name, value)
		}
		if _, exists := seen[value]; exists {
			return fmt.Errorf("%s %q is duplicated", name, value)
		}
		seen[value] = struct{}{}
	}
	return nil
}

func containsAny[T comparable](values []T, candidates ...T) bool {
	for _, candidate := range candidates {
		if slices.Contains(values, candidate) {
			return true
		}
	}
	return false
}
