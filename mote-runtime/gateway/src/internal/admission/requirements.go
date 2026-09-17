package admission

import (
	"fmt"
	"slices"
	"strings"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

// requirements is the one semantic view of a request used by capability
// admission.  It is reduced from every typed input field; callers do not get
// to maintain a second, manually mirrored modality or feature list.
type requirements struct {
	features         []api.Feature
	inputModalities  []api.Modality
	outputModalities []api.Modality
}

func reduceLLMRequirements(request api.LLMRequest) (requirements, error) {
	declared, err := validateDeclaredFeatures(request.Features, true)
	if err != nil {
		return requirements{}, err
	}
	result := requirements{features: canonicalFeatures(
		declared,
		hasToolSemantics(request.Input),
		request.Input.ResponseFormat != nil && request.Input.ResponseFormat.Type == "json_schema",
	)}

	switch request.Input.Kind {
	case "generate":
		result.inputModalities, err = llmInputModalities(request.Input)
		if err == nil && len(result.inputModalities) == 0 {
			err = fmt.Errorf("generate input has no content modality")
		}
		result.outputModalities = []api.Modality{api.ModalityText}
	case "realtime":
		// A realtime session always receives an audio stream. Every other text
		// or content field is reduced as an additional input requirement before
		// the model capability intersection is evaluated.
		result.inputModalities, err = llmInputModalities(request.Input)
		result.inputModalities = appendUnique(result.inputModalities, api.ModalityAudio)
		result.outputModalities = append([]api.Modality(nil), request.Input.Modalities...)
		if len(result.outputModalities) == 0 {
			result.outputModalities = []api.Modality{api.ModalityAudio}
		}
	default:
		return requirements{}, fmt.Errorf("unsupported LLM input kind %q", request.Input.Kind)
	}
	if err != nil {
		return requirements{}, err
	}
	return result, nil
}

func llmInputModalities(input api.LLMInput) ([]api.Modality, error) {
	modalities, err := messageModalities(input.Messages)
	if err != nil {
		return nil, err
	}
	if strings.TrimSpace(input.SystemPrompt) != "" || strings.TrimSpace(input.Instructions) != "" {
		modalities = appendUnique(modalities, api.ModalityText)
	}
	return modalities, nil
}

func messageModalities(messages []api.Message) ([]api.Modality, error) {
	var modalities []api.Modality
	for messageIndex, message := range messages {
		for partIndex, part := range message.Content {
			modality, err := contentPartModality(part)
			if err != nil {
				return nil, fmt.Errorf("message[%d].content[%d]: %w", messageIndex, partIndex, err)
			}
			modalities = appendUnique(modalities, modality)
		}
	}
	return modalities, nil
}

func contentPartModality(part api.ContentPart) (api.Modality, error) {
	if part.Type == api.ArtifactKindText {
		if part.Artifact != nil {
			return "", fmt.Errorf("text content must not carry an artifact")
		}
		return api.ModalityText, nil
	}
	if part.Artifact == nil {
		return "", fmt.Errorf("%s content requires an artifact", part.Type)
	}
	modality, ok := api.ArtifactModality(part.Type)
	if !ok || modality == api.ModalityMusic {
		return "", fmt.Errorf("unsupported message content type %q", part.Type)
	}
	artifactModality, ok := api.ArtifactModality(part.Artifact.Kind)
	if !ok {
		return "", fmt.Errorf("artifact kind %q has no canonical modality", part.Artifact.Kind)
	}
	if artifactModality != modality {
		return "", fmt.Errorf("artifact kind %q does not match content type %q", part.Artifact.Kind, part.Type)
	}
	return modality, nil
}

func reduceMediaRequirements(request api.MediaRequest) (requirements, error) {
	features, err := validateDeclaredFeatures(request.Features, false)
	if err != nil {
		return requirements{}, err
	}
	result := requirements{features: canonicalFeatures(features, false, false)}
	shape, ok := mediaShapes[operationValue(request.Operation)]
	if !ok {
		return requirements{}, fmt.Errorf("unsupported media operation %q", operationValue(request.Operation))
	}
	if strings.TrimSpace(request.Input.Prompt) != "" {
		result.inputModalities = appendUnique(result.inputModalities, api.ModalityText)
	}
	if strings.TrimSpace(request.Input.Text) != "" {
		result.inputModalities = appendUnique(result.inputModalities, api.ModalityText)
	}
	result.inputModalities, err = appendArtifactRequirement(result.inputModalities, request.Input.Source, "source")
	if err != nil {
		return requirements{}, err
	}
	result.inputModalities, err = appendArtifactRequirement(result.inputModalities, request.Input.Media, "media")
	if err != nil {
		return requirements{}, err
	}
	result.outputModalities = []api.Modality{shape.outputModality}
	return result, nil
}

func appendArtifactRequirement(modalities []api.Modality, artifact *api.ArtifactRef, label string) ([]api.Modality, error) {
	if artifact == nil {
		return modalities, nil
	}
	modality, ok := api.ArtifactModality(artifact.Kind)
	if !ok {
		return nil, fmt.Errorf("%s artifact kind %q has no canonical modality", label, artifact.Kind)
	}
	return appendUnique(modalities, modality), nil
}

func validateDeclaredFeatures(features []api.Feature, llm bool) ([]api.Feature, error) {
	seen := make(map[api.Feature]struct{}, len(features))
	for _, feature := range features {
		if _, exists := seen[feature]; exists {
			return nil, fmt.Errorf("duplicate feature %q", feature)
		}
		seen[feature] = struct{}{}
		if err := validateProfileFeature(feature, llm); err != nil {
			return nil, err
		}
	}
	return append([]api.Feature(nil), features...), nil
}

// canonicalFeatures returns the de-duplicated union of explicit and
// structurally implied capabilities.  Explicit features are declarations, not
// a checklist that must exactly mirror every input trigger.
func canonicalFeatures(declared []api.Feature, toolCalls, structured bool) []api.Feature {
	required := map[api.Feature]bool{
		api.FeatureToolCalls:  toolCalls,
		api.FeatureStructured: structured,
	}
	for _, feature := range declared {
		required[feature] = true
	}
	ordered := []api.Feature{
		api.FeatureToolCalls,
		api.FeatureStructured,
		api.FeaturePromptCache,
		api.FeatureUsage,
	}
	result := make([]api.Feature, 0, len(required))
	for _, feature := range ordered {
		if required[feature] {
			result = append(result, feature)
		}
	}
	return result
}

func hasToolSemantics(input api.LLMInput) bool {
	if len(input.Tools) > 0 || input.ToolChoice != nil {
		return true
	}
	for _, message := range input.Messages {
		if message.Role == "tool" || message.ToolCallID != "" || len(message.ToolCalls) > 0 {
			return true
		}
	}
	return false
}

func appendUnique[T comparable](values []T, value T) []T {
	if slices.Contains(values, value) {
		return values
	}
	return append(values, value)
}
