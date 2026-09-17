package service

import (
	"fmt"
	"slices"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

// ReasoningCapability declares the reasoning forms exposed by one service.
// Service facts are intentionally independent from protocol facts.
type ReasoningCapability struct {
	Thinking api.ThinkingMode
	Efforts  []api.ReasoningEffort
}

// OperationCapability declares one service API surface.
type OperationCapability struct {
	Modes     []api.DeliveryMode
	Features  []api.Feature
	Reasoning []ReasoningCapability
}

// Facts is the service-owned fact surface admission reads.
type Facts interface {
	Name() string
	SupportsProtocol(string) bool
	SupportsModel(string) bool
	SupportsOperation(api.Operation, api.DeliveryMode) bool
	SupportsFeature(api.Operation, api.Feature) bool
	SupportsReasoning(api.Operation, *api.ReasoningConfig) bool
}

// Descriptor is an immutable declaration of one configured model service.
type Descriptor struct {
	identifier string
	protocols  map[string]struct{}
	models     map[string]struct{}
	operations map[api.Operation]OperationCapability
}

// DescriptorConfig is the complete, service-owned deployment declaration.
// Models contains exact Router-visible BaseModel identities; it is an
// allowlist, not a routing or fallback table.
type DescriptorConfig struct {
	Kind       string
	Protocols  []string
	Models     []string
	Operations map[api.Operation]OperationCapability
}

// NewDescriptor validates and freezes a service-owned capability declaration.
func NewDescriptor(config DescriptorConfig) (Descriptor, error) {
	if err := api.ValidateServiceKind(config.Kind); err != nil {
		return Descriptor{}, fmt.Errorf("invalid service descriptor: %w", err)
	}
	protocolSet, err := freezeProtocols(config.Kind, config.Protocols)
	if err != nil {
		return Descriptor{}, err
	}
	modelSet, err := freezeModels(config.Kind, config.Models)
	if err != nil {
		return Descriptor{}, err
	}
	if len(config.Operations) == 0 {
		return Descriptor{}, fmt.Errorf("service %q must declare an operation", config.Kind)
	}
	if err := validateOperations(config.Kind, config.Operations); err != nil {
		return Descriptor{}, err
	}
	return Descriptor{
		identifier: config.Kind,
		protocols:  protocolSet,
		models:     modelSet,
		operations: cloneOperations(config.Operations),
	}, nil
}

// Name returns the immutable service identity.
func (descriptor Descriptor) Name() string { return descriptor.identifier }

// SupportsProtocol reports whether this configured service exposes the exact
// selected wire protocol. Protocol identifiers are never aliased or inferred
// from an operation declaration.
func (descriptor Descriptor) SupportsProtocol(protocolID string) bool {
	_, ok := descriptor.protocols[protocolID]
	return ok
}

// SupportsModel reports whether this configured service can deploy the exact
// model already selected by Router. It never selects an alternative model.
func (descriptor Descriptor) SupportsModel(baseModel string) bool {
	_, ok := descriptor.models[baseModel]
	return ok
}

// SupportsOperation reports support for an operation and delivery mode.
func (descriptor Descriptor) SupportsOperation(operation api.Operation, mode api.DeliveryMode) bool {
	capability, ok := descriptor.operations[operation]
	return ok && slices.Contains(capability.Modes, mode)
}

// SupportsFeature reports service support for a required model feature.
func (descriptor Descriptor) SupportsFeature(operation api.Operation, feature api.Feature) bool {
	capability, ok := descriptor.operations[operation]
	return ok && slices.Contains(capability.Features, feature)
}

// SupportsReasoning reports lossless service support for a normalized
// reasoning preference.
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

func freezeProtocols(kind string, protocols []string) (map[string]struct{}, error) {
	if len(protocols) == 0 {
		return nil, fmt.Errorf("service %q must declare a protocol", kind)
	}
	result := make(map[string]struct{}, len(protocols))
	for _, protocolID := range protocols {
		if err := api.ValidateProtocolID(protocolID); err != nil {
			return nil, fmt.Errorf("service %q: %w", kind, err)
		}
		if _, exists := result[protocolID]; exists {
			return nil, fmt.Errorf("service %q declares duplicate protocol %q", kind, protocolID)
		}
		result[protocolID] = struct{}{}
	}
	return result, nil
}

func freezeModels(kind string, models []string) (map[string]struct{}, error) {
	if len(models) == 0 {
		return nil, fmt.Errorf("service %q must declare an exact model", kind)
	}
	result := make(map[string]struct{}, len(models))
	for _, baseModel := range models {
		if err := api.ValidateBaseModel(baseModel); err != nil {
			return nil, fmt.Errorf("service %q: invalid model %q: %w", kind, baseModel, err)
		}
		if _, exists := result[baseModel]; exists {
			return nil, fmt.Errorf("service %q declares duplicate model %q", kind, baseModel)
		}
		result[baseModel] = struct{}{}
	}
	return result, nil
}

func validateOperations(identifier string, operations map[api.Operation]OperationCapability) error {
	keys := make([]api.Operation, 0, len(operations))
	for operation := range operations {
		keys = append(keys, operation)
	}
	slices.Sort(keys)
	for _, operation := range keys {
		if !operation.IsValid() {
			return fmt.Errorf("service %q declares unsupported operation %q", identifier, operation)
		}
		capability := operations[operation]
		if len(capability.Modes) == 0 {
			return fmt.Errorf("service %q operation %q must declare a delivery mode", identifier, operation)
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
			return fmt.Errorf("service %q operation %q declares unsupported delivery mode %q", identifier, operation, mode)
		}
		if _, exists := seen[mode]; exists {
			return fmt.Errorf("service %q operation %q declares duplicate delivery mode %q", identifier, operation, mode)
		}
		seen[mode] = struct{}{}
	}
	return nil
}

func validateFeatures(identifier string, operation api.Operation, features []api.Feature) error {
	seen := make(map[api.Feature]struct{}, len(features))
	for _, feature := range features {
		if !feature.IsValid() {
			return fmt.Errorf("service %q operation %q declares unsupported feature %q", identifier, operation, feature)
		}
		if _, exists := seen[feature]; exists {
			return fmt.Errorf("service %q operation %q declares duplicate feature %q", identifier, operation, feature)
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
		return fmt.Errorf("service %q operation %q must not declare reasoning capability", identifier, operation)
	}
	seenThinking := make(map[api.ThinkingMode]struct{}, len(reasoning))
	for _, candidate := range reasoning {
		if !candidate.Thinking.IsValid() {
			return fmt.Errorf("service %q operation %q declares unsupported thinking mode %q", identifier, operation, candidate.Thinking)
		}
		if _, exists := seenThinking[candidate.Thinking]; exists {
			return fmt.Errorf("service %q operation %q declares duplicate thinking mode %q", identifier, operation, candidate.Thinking)
		}
		seenThinking[candidate.Thinking] = struct{}{}
		seenEfforts := make(map[api.ReasoningEffort]struct{}, len(candidate.Efforts))
		for _, effort := range candidate.Efforts {
			if !effort.IsValid() {
				return fmt.Errorf("service %q operation %q thinking mode %q declares unsupported effort %q", identifier, operation, candidate.Thinking, effort)
			}
			if _, exists := seenEfforts[effort]; exists {
				return fmt.Errorf("service %q operation %q thinking mode %q declares duplicate effort %q", identifier, operation, candidate.Thinking, effort)
			}
			seenEfforts[effort] = struct{}{}
		}
		if candidate.Thinking == api.ThinkingDisabled && len(candidate.Efforts) != 0 {
			return fmt.Errorf("service %q operation %q disabled thinking must not declare efforts", identifier, operation)
		}
	}
	return nil
}
