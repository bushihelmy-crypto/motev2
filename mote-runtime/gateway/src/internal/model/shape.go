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
	allowedInputs    []api.Modality
	allowedOutputs   []api.Modality
	requiredOutput   api.Modality
	embedding        bool
	allowsGeneration bool
}

var operationShapePolicies = map[api.Operation]operationShapePolicy{
	api.OperationGenerate: {
		allowedInputs:    []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs:   []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowsGeneration: true,
	},
	api.OperationEmbedding: {
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs: []api.Modality{api.ModalityEmbedding},
		requiredOutput: api.ModalityEmbedding,
		embedding:      true,
	},
	api.OperationRerank: {
		allowedInputs:  []api.Modality{api.ModalityText},
		allowedOutputs: []api.Modality{api.ModalityText},
		requiredOutput: api.ModalityText,
	},
	api.OperationImageGeneration: {
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs: []api.Modality{api.ModalityText, api.ModalityImage},
		requiredOutput: api.ModalityImage,
	},
	api.OperationAudioGeneration: {
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityAudio},
		allowedOutputs: []api.Modality{api.ModalityText, api.ModalityAudio},
		requiredOutput: api.ModalityAudio,
	},
	api.OperationAudioTranscription: {
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs: []api.Modality{api.ModalityText},
		requiredOutput: api.ModalityText,
	},
	api.OperationMusicGeneration: {
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityAudio},
		allowedOutputs: []api.Modality{api.ModalityText, api.ModalityAudio, api.ModalityMusic},
		requiredOutput: api.ModalityMusic,
	},
	api.OperationVideoGeneration: {
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs: []api.Modality{api.ModalityText, api.ModalityVideo},
		requiredOutput: api.ModalityVideo,
	},
	api.OperationRealtime: {
		allowedInputs:    []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs:   []api.Modality{api.ModalityText, api.ModalityAudio},
		allowsGeneration: true,
	},
}

// validateCapabilityShape validates the one operation-shape rule.
// Explicit multimodal model facts remain valid when they are semantically
// compatible with the operation; profile-specific invocation limits belong to
// admission.
func validateCapabilityShape(shape capabilityShape, policy operationShapePolicy) error {
	if err := validateShapeSet("input modality", shape.InputModalities, policy.allowedInputs); err != nil {
		return err
	}
	if err := validateShapeSet("output modality", shape.OutputModalities, policy.allowedOutputs); err != nil {
		return err
	}
	if shape.Operation == api.OperationRealtime &&
		!containsAny(shape.InputModalities, api.ModalityText, api.ModalityAudio) {
		return fmt.Errorf("realtime must accept text or audio input")
	}
	if policy.requiredOutput != "" && !slices.Contains(shape.OutputModalities, policy.requiredOutput) {
		return fmt.Errorf("%s must produce %s", shape.Operation, policy.requiredOutput)
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
	for _, value := range values {
		if !slices.Contains(allowed, value) {
			return fmt.Errorf("%s %q is not allowed for this operation", name, value)
		}
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
