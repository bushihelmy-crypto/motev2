package protocol

import (
	"fmt"
	"slices"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

// ReasoningCapability declares the reasoning forms this protocol can encode.
// It is protocol-owned: a service declaration has a distinct type and is not
// silently shared through a neutral capability matrix.
type ReasoningCapability struct {
	Thinking api.ThinkingMode
	Efforts  []api.ReasoningEffort
}

// OperationCapability declares one protocol operation surface. Delivery modes,
// features, and reasoning are wire concerns and therefore belong here.
type OperationCapability struct {
	Modes     []api.DeliveryMode
	Features  []api.Feature
	Reasoning []ReasoningCapability
}

// Facts is the protocol-owned fact surface admission reads. Keeping this
// interface beside the descriptor prevents a shared capability owner from
// becoming a second source of protocol truth.
type Facts interface {
	Name() string
	SupportsOperation(api.Operation, api.DeliveryMode) bool
	SupportsFeature(api.Operation, api.Feature) bool
	SupportsReasoning(api.Operation, *api.ReasoningConfig) bool
}

// Descriptor is an immutable declaration of one configured wire protocol.
// It contains no endpoint, credential, or service state.
type Descriptor struct {
	identifier string
	operations map[api.Operation]OperationCapability
}

// NewDescriptor validates and freezes a protocol-owned capability declaration.
func NewDescriptor(identifier string, operations map[api.Operation]OperationCapability) (Descriptor, error) {
	if err := api.ValidateProtocolID(identifier); err != nil {
		return Descriptor{}, fmt.Errorf("invalid protocol descriptor: %w", err)
	}
	if len(operations) == 0 {
		return Descriptor{}, fmt.Errorf("protocol %q must declare an operation", identifier)
	}
	if err := validateOperations(identifier, operations); err != nil {
		return Descriptor{}, err
	}
	return Descriptor{identifier: identifier, operations: cloneOperations(operations)}, nil
}

// Name returns the immutable protocol identity.
func (descriptor Descriptor) Name() string { return descriptor.identifier }

// SupportsOperation reports whether this protocol can carry the operation in
// the requested delivery mode.
func (descriptor Descriptor) SupportsOperation(operation api.Operation, mode api.DeliveryMode) bool {
	capability, ok := descriptor.operations[operation]
	return ok && slices.Contains(capability.Modes, mode)
}

// SupportsFeature reports protocol support for a required model feature.
func (descriptor Descriptor) SupportsFeature(operation api.Operation, feature api.Feature) bool {
	capability, ok := descriptor.operations[operation]
	return ok && slices.Contains(capability.Features, feature)
}

// SupportsReasoning reports whether the protocol can express the normalized
// reasoning preference without silently changing its meaning.
func (descriptor Descriptor) SupportsReasoning(operation api.Operation, reasoning *api.ReasoningConfig) bool {
	if reasoning == nil {
		return true
	}
	capability, ok := descriptor.operations[operation]
	if !ok {
		return false
	}
	for _, candidate := range capability.Reasoning {
		if candidate.Thinking == reasoning.Thinking && (reasoning.Effort == "" || slices.Contains(candidate.Efforts, reasoning.Effort)) {
			return true
		}
	}
	return false
}

func cloneOperations(input map[api.Operation]OperationCapability) map[api.Operation]OperationCapability {
	result := make(map[api.Operation]OperationCapability, len(input))
	for operation, capability := range input {
		result[operation] = OperationCapability{
			Modes:     append([]api.DeliveryMode(nil), capability.Modes...),
			Features:  append([]api.Feature(nil), capability.Features...),
			Reasoning: cloneReasoning(capability.Reasoning),
		}
	}
	return result
}

func cloneReasoning(input []ReasoningCapability) []ReasoningCapability {
	result := make([]ReasoningCapability, len(input))
	for index, reasoning := range input {
		result[index] = ReasoningCapability{
			Thinking: reasoning.Thinking,
			Efforts:  append([]api.ReasoningEffort(nil), reasoning.Efforts...),
		}
	}
	return result
}

func validateOperations(identifier string, operations map[api.Operation]OperationCapability) error {
	keys := make([]api.Operation, 0, len(operations))
	for operation := range operations {
		keys = append(keys, operation)
	}
	slices.Sort(keys)
	for _, operation := range keys {
		if !operation.IsValid() {
			return fmt.Errorf("protocol %q declares unsupported operation %q", identifier, operation)
		}
		capability := operations[operation]
		if len(capability.Modes) == 0 {
			return fmt.Errorf("protocol %q operation %q must declare a delivery mode", identifier, operation)
		}
		if err := validateModes(identifier, operation, capability.Modes); err != nil {
			return err
		}
		if err := validateFeatures(identifier, operation, capability.Features); err != nil {
			return err
		}
		if err := validateReasoning(identifier, operation, capability.Reasoning); err != nil {
			return err
		}
	}
	return nil
}

func validateModes(identifier string, operation api.Operation, modes []api.DeliveryMode) error {
	seen := make(map[api.DeliveryMode]struct{}, len(modes))
	for _, mode := range modes {
		if !mode.IsValid() {
			return fmt.Errorf("protocol %q operation %q declares unsupported delivery mode %q", identifier, operation, mode)
		}
		if _, exists := seen[mode]; exists {
			return fmt.Errorf("protocol %q operation %q declares duplicate delivery mode %q", identifier, operation, mode)
		}
		seen[mode] = struct{}{}
	}
	return nil
}

func validateFeatures(identifier string, operation api.Operation, features []api.Feature) error {
	seen := make(map[api.Feature]struct{}, len(features))
	for _, feature := range features {
		if !feature.IsValid() {
			return fmt.Errorf("protocol %q operation %q declares unsupported feature %q", identifier, operation, feature)
		}
		if _, exists := seen[feature]; exists {
			return fmt.Errorf("protocol %q operation %q declares duplicate feature %q", identifier, operation, feature)
		}
		seen[feature] = struct{}{}
	}
	return nil
}

func validateReasoning(identifier string, operation api.Operation, reasoning []ReasoningCapability) error {
	if len(reasoning) == 0 {
		return nil
	}
	if !operation.IsLLM() {
		return fmt.Errorf("protocol %q operation %q must not declare reasoning capability", identifier, operation)
	}
	seenThinking := make(map[api.ThinkingMode]struct{}, len(reasoning))
	for _, candidate := range reasoning {
		if !candidate.Thinking.IsValid() {
			return fmt.Errorf("protocol %q operation %q declares unsupported thinking mode %q", identifier, operation, candidate.Thinking)
		}
		if _, exists := seenThinking[candidate.Thinking]; exists {
			return fmt.Errorf("protocol %q operation %q declares duplicate thinking mode %q", identifier, operation, candidate.Thinking)
		}
		seenThinking[candidate.Thinking] = struct{}{}
		seenEfforts := make(map[api.ReasoningEffort]struct{}, len(candidate.Efforts))
		for _, effort := range candidate.Efforts {
			if !effort.IsValid() {
				return fmt.Errorf("protocol %q operation %q thinking mode %q declares unsupported effort %q", identifier, operation, candidate.Thinking, effort)
			}
			if _, exists := seenEfforts[effort]; exists {
				return fmt.Errorf("protocol %q operation %q thinking mode %q declares duplicate effort %q", identifier, operation, candidate.Thinking, effort)
			}
			seenEfforts[effort] = struct{}{}
		}
		if candidate.Thinking == api.ThinkingDisabled && len(candidate.Efforts) != 0 {
			return fmt.Errorf("protocol %q operation %q disabled thinking must not declare efforts", identifier, operation)
		}
	}
	return nil
}
