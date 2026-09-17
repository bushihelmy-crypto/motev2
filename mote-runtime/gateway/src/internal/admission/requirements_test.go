package admission

import (
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

func TestLLMRequirementsRejectInvalidDeclaredAndStructuralInputs(t *testing.T) {
	artifact := func(kind string) *api.ArtifactRef {
		return &api.ArtifactRef{Kind: kind}
	}
	tests := []struct {
		name    string
		request api.LLMRequest
		want    string
	}{
		{
			name:    "duplicate feature",
			request: api.LLMRequest{Features: []api.Feature{api.FeatureUsage, api.FeatureUsage}},
			want:    "duplicate feature",
		},
		{
			name:    "unknown feature",
			request: api.LLMRequest{Features: []api.Feature{"private"}},
			want:    "unsupported feature",
		},
		{
			name:    "unknown input kind",
			request: api.LLMRequest{Input: api.LLMInput{Kind: "future"}},
			want:    "unsupported LLM input kind",
		},
		{
			name: "text artifact",
			request: api.LLMRequest{Input: api.LLMInput{Kind: "generate", Messages: []api.Message{{Content: []api.ContentPart{
				{Type: api.ArtifactKindText, Artifact: artifact(api.ArtifactKindText)},
			}}}}},
			want: "text content must not carry an artifact",
		},
		{
			name: "missing artifact",
			request: api.LLMRequest{Input: api.LLMInput{Kind: "generate", Messages: []api.Message{{Content: []api.ContentPart{
				{Type: api.ArtifactKindImage},
			}}}}},
			want: "requires an artifact",
		},
		{
			name: "unsupported content type",
			request: api.LLMRequest{Input: api.LLMInput{Kind: "generate", Messages: []api.Message{{Content: []api.ContentPart{
				{Type: api.ArtifactKindMusic, Artifact: artifact(api.ArtifactKindMusic)},
			}}}}},
			want: "unsupported message content type",
		},
		{
			name: "unknown artifact kind",
			request: api.LLMRequest{Input: api.LLMInput{Kind: "generate", Messages: []api.Message{{Content: []api.ContentPart{
				{Type: api.ArtifactKindImage, Artifact: artifact("future")},
			}}}}},
			want: "has no canonical modality",
		},
		{
			name: "mismatched artifact kind",
			request: api.LLMRequest{Input: api.LLMInput{Kind: "generate", Messages: []api.Message{{Content: []api.ContentPart{
				{Type: api.ArtifactKindImage, Artifact: artifact(api.ArtifactKindAudio)},
			}}}}},
			want: "does not match content type",
		},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := reduceLLMRequirements(testCase.request)
			if err == nil || !strings.Contains(err.Error(), testCase.want) {
				t.Fatalf("expected %q, got %v", testCase.want, err)
			}
		})
	}
}

func TestLLMRequirementsDeriveUniqueModalitiesAndMessageToolSemantics(t *testing.T) {
	request := api.LLMRequest{Input: api.LLMInput{
		Kind: "generate",
		Messages: []api.Message{{
			Role: "tool",
			Content: []api.ContentPart{
				{Type: api.ArtifactKindText, Text: "one"},
				{Type: api.ArtifactKindText, Text: "two"},
			},
		}},
	}}
	required, err := reduceLLMRequirements(request)
	if err != nil {
		t.Fatal(err)
	}
	if len(required.inputModalities) != 1 || required.inputModalities[0] != api.ModalityText {
		t.Fatalf("duplicate text modalities were not reduced: %+v", required.inputModalities)
	}
	if len(required.features) != 1 || required.features[0] != api.FeatureToolCalls {
		t.Fatalf("tool message did not imply tool_calls: %+v", required.features)
	}
}

func TestMediaRequirementsValidateProfileAndAllInputFields(t *testing.T) {
	imageOperation := api.OperationImageGeneration
	base := api.MediaRequest{
		Operation: &imageOperation,
		Input:     api.MediaInput{Kind: "image_generation", Prompt: "draw"},
	}
	tests := []struct {
		name   string
		mutate func(*api.MediaRequest)
		want   string
	}{
		{name: "duplicate feature", mutate: func(request *api.MediaRequest) {
			request.Features = []api.Feature{api.FeatureUsage, api.FeatureUsage}
		}, want: "duplicate feature"},
		{name: "feature outside media profile", mutate: func(request *api.MediaRequest) {
			request.Features = []api.Feature{api.FeatureToolCalls}
		}, want: "not supported by execution_media"},
		{name: "unknown feature", mutate: func(request *api.MediaRequest) {
			request.Features = []api.Feature{"private"}
		}, want: "unsupported feature"},
		{name: "unsupported operation", mutate: func(request *api.MediaRequest) {
			request.Operation = nil
		}, want: "unsupported media operation"},
		{name: "unknown media artifact", mutate: func(request *api.MediaRequest) {
			request.Input.Media = &api.ArtifactRef{Kind: "future"}
		}, want: "media artifact kind"},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			request := base
			testCase.mutate(&request)
			_, err := reduceMediaRequirements(request)
			if err == nil || !strings.Contains(err.Error(), testCase.want) {
				t.Fatalf("expected %q, got %v", testCase.want, err)
			}
		})
	}

	audioOperation := api.OperationAudioGeneration
	required, err := reduceMediaRequirements(api.MediaRequest{
		Operation: &audioOperation,
		Features:  []api.Feature{api.FeatureUsage},
		Input:     api.MediaInput{Kind: "audio_generation", Text: "speak"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(required.inputModalities) != 1 || required.inputModalities[0] != api.ModalityText || len(required.features) != 1 {
		t.Fatalf("media text and feature requirements were not reduced: %+v", required)
	}
}

func TestLLMRequirementsDeriveEveryToolAndStructuredTrigger(t *testing.T) {
	base := api.LLMRequest{Input: api.LLMInput{
		Kind:     "generate",
		Messages: []api.Message{{Role: "user", Content: []api.ContentPart{{Type: api.ArtifactKindText, Text: "hello"}}}},
	}}
	tests := []struct {
		name   string
		mutate func(*api.LLMRequest)
		want   []api.Feature
	}{
		{name: "tools", mutate: func(request *api.LLMRequest) {
			request.Input.Tools = []api.ToolDefinition{{Name: "lookup"}}
		}, want: []api.Feature{api.FeatureToolCalls}},
		{name: "tool choice", mutate: func(request *api.LLMRequest) {
			request.Input.ToolChoice = &api.ToolChoice{Mode: "auto"}
		}, want: []api.Feature{api.FeatureToolCalls}},
		{name: "tool role", mutate: func(request *api.LLMRequest) {
			request.Input.Messages[0].Role = "tool"
		}, want: []api.Feature{api.FeatureToolCalls}},
		{name: "tool call id", mutate: func(request *api.LLMRequest) {
			request.Input.Messages[0].ToolCallID = "call-1"
		}, want: []api.Feature{api.FeatureToolCalls}},
		{name: "tool calls", mutate: func(request *api.LLMRequest) {
			request.Input.Messages[0].ToolCalls = []api.ToolCall{{CallID: "call-1", Name: "lookup"}}
		}, want: []api.Feature{api.FeatureToolCalls}},
		{name: "json schema", mutate: func(request *api.LLMRequest) {
			request.Input.ResponseFormat = &api.ResponseFormat{Type: "json_schema"}
		}, want: []api.Feature{api.FeatureStructured}},
		{name: "ordinary json object", mutate: func(request *api.LLMRequest) {
			request.Input.ResponseFormat = &api.ResponseFormat{Type: "json_object"}
		}, want: nil},
		{name: "explicit and inferred union", mutate: func(request *api.LLMRequest) {
			request.Features = []api.Feature{api.FeatureUsage, api.FeatureStructured, api.FeaturePromptCache, api.FeatureToolCalls}
			request.Input.Tools = []api.ToolDefinition{{Name: "lookup"}}
			request.Input.ResponseFormat = &api.ResponseFormat{Type: "json_schema"}
		}, want: []api.Feature{api.FeatureToolCalls, api.FeatureStructured, api.FeaturePromptCache, api.FeatureUsage}},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			request := base
			request.Input.Messages = append([]api.Message(nil), base.Input.Messages...)
			testCase.mutate(&request)
			required, err := reduceLLMRequirements(request)
			if err != nil {
				t.Fatal(err)
			}
			if !slices.Equal(required.features, testCase.want) {
				t.Fatalf("features = %v, want %v", required.features, testCase.want)
			}
		})
	}
}

func TestLLMRequirementsReduceAllContentModalitiesOnce(t *testing.T) {
	artifact := func(kind string) *api.ArtifactRef { return &api.ArtifactRef{Kind: kind} }
	request := api.LLMRequest{Input: api.LLMInput{
		Kind: "generate",
		Messages: []api.Message{
			{Content: []api.ContentPart{
				{Type: api.ArtifactKindVideo, Artifact: artifact(api.ArtifactKindVideo)},
				{Type: api.ArtifactKindText, Text: "one"},
				{Type: api.ArtifactKindImage, Artifact: artifact(api.ArtifactKindImage)},
			}},
			{Content: []api.ContentPart{
				{Type: api.ArtifactKindAudio, Artifact: artifact(api.ArtifactKindAudio)},
				{Type: api.ArtifactKindText, Text: "two"},
				{Type: api.ArtifactKindImage, Artifact: artifact(api.ArtifactKindImage)},
			}},
		},
		SystemPrompt: "policy",
	}}
	required, err := reduceLLMRequirements(request)
	if err != nil {
		t.Fatal(err)
	}
	wantInput := []api.Modality{api.ModalityVideo, api.ModalityText, api.ModalityImage, api.ModalityAudio}
	if !reflect.DeepEqual(required.inputModalities, wantInput) {
		t.Fatalf("input modalities = %v, want %v", required.inputModalities, wantInput)
	}
	if !reflect.DeepEqual(required.outputModalities, []api.Modality{api.ModalityText}) {
		t.Fatalf("generate output modalities = %v", required.outputModalities)
	}
}

func TestRealtimeRequirementsDeriveStreamAndRequestedOutputModalities(t *testing.T) {
	request := api.LLMRequest{Input: api.LLMInput{
		Kind:         "realtime",
		Instructions: "answer briefly",
		Modalities:   []api.Modality{api.ModalityText, api.ModalityAudio},
		Tools:        []api.ToolDefinition{{Name: "lookup"}},
	}}
	required, err := reduceLLMRequirements(request)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(required.inputModalities, []api.Modality{api.ModalityText, api.ModalityAudio}) {
		t.Fatalf("realtime inputs = %v", required.inputModalities)
	}
	if !reflect.DeepEqual(required.outputModalities, request.Input.Modalities) {
		t.Fatalf("realtime outputs = %v, want %v", required.outputModalities, request.Input.Modalities)
	}
	if !reflect.DeepEqual(required.features, []api.Feature{api.FeatureToolCalls}) {
		t.Fatalf("realtime features = %v", required.features)
	}

	request.Input.Instructions = ""
	request.Input.Modalities = nil
	request.Input.Tools = nil
	required, err = reduceLLMRequirements(request)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(required.inputModalities, []api.Modality{api.ModalityAudio}) ||
		!reflect.DeepEqual(required.outputModalities, []api.Modality{api.ModalityAudio}) {
		t.Fatalf("realtime defaults = %+v", required)
	}
}

func TestMediaRequirementsMapEveryOperationToItsActualModalities(t *testing.T) {
	tests := []struct {
		operation api.Operation
		input     api.MediaInput
		wantInput []api.Modality
		wantOut   api.Modality
	}{
		{operation: api.OperationImageGeneration, input: api.MediaInput{Prompt: "draw", Source: &api.ArtifactRef{Kind: api.ArtifactKindImage}}, wantInput: []api.Modality{api.ModalityText, api.ModalityImage}, wantOut: api.ModalityImage},
		{operation: api.OperationAudioGeneration, input: api.MediaInput{Text: "speak"}, wantInput: []api.Modality{api.ModalityText}, wantOut: api.ModalityAudio},
		{operation: api.OperationMusicGeneration, input: api.MediaInput{Prompt: "compose"}, wantInput: []api.Modality{api.ModalityText}, wantOut: api.ModalityMusic},
		{operation: api.OperationVideoGeneration, input: api.MediaInput{Prompt: "animate", Source: &api.ArtifactRef{Kind: api.ArtifactKindVideo}}, wantInput: []api.Modality{api.ModalityText, api.ModalityVideo}, wantOut: api.ModalityVideo},
		{operation: api.OperationAudioTranscription, input: api.MediaInput{Media: &api.ArtifactRef{Kind: api.ArtifactKindAudio}}, wantInput: []api.Modality{api.ModalityAudio}, wantOut: api.ModalityText},
	}
	for _, testCase := range tests {
		t.Run(string(testCase.operation), func(t *testing.T) {
			required, err := reduceMediaRequirements(api.MediaRequest{Operation: &testCase.operation, Input: testCase.input})
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(required.inputModalities, testCase.wantInput) ||
				!reflect.DeepEqual(required.outputModalities, []api.Modality{testCase.wantOut}) {
				t.Fatalf("requirements = %+v, want input=%v output=%v", required, testCase.wantInput, testCase.wantOut)
			}
		})
	}
}
