package model

import (
	"fmt"
	"slices"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

// OperationShape is the typed, service-neutral shape of one model operation.
// It is shared by catalog generation and runtime validation so an operation
// cannot be published in a shape that runtime would later accept differently.
type OperationShape struct {
	Operation        api.Operation
	Modes            []api.DeliveryMode
	InputModalities  []api.Modality
	OutputModalities []api.Modality
	HasGeneration    bool
	HasEmbedding     bool
}

type operationShapePolicy struct {
	allowedModes     []api.DeliveryMode
	allowedInputs    []api.Modality
	allowedOutputs   []api.Modality
	requiredOutput   api.Modality
	exactOutput      bool
	embedding        bool
	allowsGeneration bool
	realtime         bool
}

var operationShapePolicies = map[api.Operation]operationShapePolicy{
	api.OperationGenerate: {
		allowedModes:     []api.DeliveryMode{api.ModeUnary, api.ModeServerStream, api.ModeAsync},
		allowedInputs:    []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs:   []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowsGeneration: true,
	},
	api.OperationEmbedding: {
		allowedModes:   []api.DeliveryMode{api.ModeUnary, api.ModeAsync},
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs: []api.Modality{api.ModalityEmbedding},
		requiredOutput: api.ModalityEmbedding,
		exactOutput:    true,
		embedding:      true,
	},
	api.OperationRerank: {
		allowedModes:   []api.DeliveryMode{api.ModeUnary, api.ModeAsync},
		allowedInputs:  []api.Modality{api.ModalityText},
		allowedOutputs: []api.Modality{api.ModalityText},
		requiredOutput: api.ModalityText,
	},
	api.OperationImageGeneration: {
		allowedModes:   []api.DeliveryMode{api.ModeUnary, api.ModeServerStream, api.ModeAsync},
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs: []api.Modality{api.ModalityText, api.ModalityImage},
		requiredOutput: api.ModalityImage,
	},
	api.OperationAudioGeneration: {
		allowedModes:   []api.DeliveryMode{api.ModeUnary, api.ModeServerStream, api.ModeAsync},
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityAudio},
		allowedOutputs: []api.Modality{api.ModalityText, api.ModalityAudio},
		requiredOutput: api.ModalityAudio,
	},
	api.OperationAudioTranscription: {
		allowedModes:   []api.DeliveryMode{api.ModeUnary, api.ModeServerStream, api.ModeAsync},
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs: []api.Modality{api.ModalityText},
		requiredOutput: api.ModalityText,
	},
	api.OperationMusicGeneration: {
		allowedModes:   []api.DeliveryMode{api.ModeUnary, api.ModeServerStream, api.ModeAsync},
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityAudio},
		allowedOutputs: []api.Modality{api.ModalityText, api.ModalityAudio, api.ModalityMusic},
		requiredOutput: api.ModalityMusic,
	},
	api.OperationVideoGeneration: {
		allowedModes:   []api.DeliveryMode{api.ModeUnary, api.ModeServerStream, api.ModeAsync},
		allowedInputs:  []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs: []api.Modality{api.ModalityText, api.ModalityVideo},
		requiredOutput: api.ModalityVideo,
	},
	api.OperationRealtime: {
		allowedModes:     []api.DeliveryMode{api.ModeDuplex},
		allowedInputs:    []api.Modality{api.ModalityText, api.ModalityImage, api.ModalityAudio, api.ModalityVideo},
		allowedOutputs:   []api.Modality{api.ModalityText, api.ModalityAudio},
		realtime:         true,
		allowsGeneration: true,
	},
}

// ValidateOperationShape validates the one canonical operation-shape rule.
// Explicit multimodal source facts remain valid when they are semantically
// compatible with the operation; profile-specific invocation limits belong to
// admission, not to this model-fact validator.
func ValidateOperationShape(shape OperationShape) error {
	policy, ok := operationShapePolicies[shape.Operation]
	if !ok {
		return fmt.Errorf("unsupported operation %q", shape.Operation)
	}
	if len(shape.Modes) == 0 || len(shape.InputModalities) == 0 || len(shape.OutputModalities) == 0 {
		return fmt.Errorf("modes and input/output modalities must not be empty")
	}
	if err := validateShapeSet("mode", shape.Modes, policy.allowedModes); err != nil {
		return err
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
	if policy.realtime {
		if !slices.Equal(shape.Modes, []api.DeliveryMode{api.ModeDuplex}) {
			return fmt.Errorf("realtime must use duplex mode only")
		}
		if !containsAny(shape.InputModalities, api.ModalityText, api.ModalityAudio) {
			return fmt.Errorf("realtime must accept text or audio input")
		}
		if !containsAny(shape.OutputModalities, api.ModalityText, api.ModalityAudio) {
			return fmt.Errorf("realtime must produce text or audio output")
		}
	} else if slices.Contains(shape.Modes, api.ModeDuplex) {
		return fmt.Errorf("only realtime may use duplex mode")
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
