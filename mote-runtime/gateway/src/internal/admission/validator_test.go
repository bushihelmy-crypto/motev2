package admission

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/protocol"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/service"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/testkit"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/ports"
)

func TestNewRejectsAnEmptyCatalog(t *testing.T) {
	validator := New(Config{Catalog: model.Catalog{}, Protocol: testkit.ProtocolCapabilities(), Service: testkit.ServiceCapabilities()})
	if _, err := validator.AdmitLLMFrame(api.LLMRequestFrame{}); Code(err) != api.ErrorInvalidRequest {
		t.Fatal("empty catalog did not fail at admission")
	}
}

func TestZeroValidatorReturnsTypedAdmissionError(t *testing.T) {
	_, err := (Validator{}).AdmitLLMFrame(api.LLMRequestFrame{})
	if Code(err) != api.ErrorInvalidRequest {
		t.Fatalf("zero validator returned the wrong error code: %T %v", err, err)
	}
}

func TestAdmitWrapsShapeFailuresAsInvalidAdmission(t *testing.T) {
	validator := testValidator(t, fixtureRecord("fixture", ports.ModelCapability{
		Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText},
		OutputModalities: []api.Modality{api.ModalityText},
	}))
	request := api.LLMRequest{
		Kind: api.RequestKindLLM, SchemaVersion: 1, OperationID: "op", BaseModel: "fixture",
		Modality: api.ModalityText, Mode: api.ModeUnary,
		Input: api.LLMInput{Kind: "generate"},
	}
	_, err := validator.AdmitLLMFrame(api.LLMRequestFrame{Request: request})
	var admissionError *AdmissionError
	if !errors.As(err, &admissionError) || Code(err) != api.ErrorInvalidRequest {
		t.Fatalf("shape failure was not a typed invalid admission: %T %v", err, err)
	}
}

func TestAdmitIntersectsProtocolAndServiceCapabilities(t *testing.T) {
	catalog, err := model.NewCatalogFromRecords([]ports.ModelRecord{fixtureRecord("fixture", ports.ModelCapability{
		Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText},
		OutputModalities: []api.Modality{api.ModalityText}, Features: []api.Feature{api.FeatureToolCalls},
	})})
	if err != nil {
		t.Fatalf("catalog: %v", err)
	}
	protocolDescriptor, err := protocol.NewDescriptor("protocol.test", map[api.Operation]protocol.OperationCapability{
		api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}, Features: []api.Feature{api.FeatureToolCalls}},
	})
	if err != nil {
		t.Fatalf("protocol descriptor: %v", err)
	}
	serviceDescriptor, err := service.NewDescriptor(service.DescriptorConfig{
		Kind: "service.test", Protocols: []string{"protocol.test"}, Models: []string{"fixture"},
		Operations: map[api.Operation]service.OperationCapability{
			api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeServerStream}, Features: []api.Feature{api.FeatureToolCalls}},
		},
	})
	if err != nil {
		t.Fatalf("service descriptor: %v", err)
	}
	validator := New(Config{Catalog: catalog, Protocol: protocolDescriptor, Service: serviceDescriptor})
	request := validAdmissionRequest("fixture", api.ModeUnary)
	request.Input.Tools = []api.ToolDefinition{{Name: "lookup", InputSchema: []byte(`{}`)}}
	request.Features = []api.Feature{api.FeatureToolCalls}
	_, err = validator.AdmitLLMFrame(llmFrame(request))
	if Code(err) != api.ErrorUnsupported {
		t.Fatalf("service intersection failure must be unsupported: %T %v", err, err)
	}
}

func TestAdmitRejectsServiceProtocolMismatch(t *testing.T) {
	catalog, err := model.NewCatalogFromRecords([]ports.ModelRecord{fixtureRecord("fixture", ports.ModelCapability{
		Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText},
		OutputModalities: []api.Modality{api.ModalityText},
	})})
	if err != nil {
		t.Fatalf("catalog: %v", err)
	}
	protocolDescriptor, err := protocol.NewDescriptor("protocol.selected", map[api.Operation]protocol.OperationCapability{
		api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}},
	})
	if err != nil {
		t.Fatalf("protocol descriptor: %v", err)
	}
	serviceDescriptor, err := service.NewDescriptor(service.DescriptorConfig{
		Kind: "service.test", Protocols: []string{"protocol.other"}, Models: []string{"fixture"},
		Operations: map[api.Operation]service.OperationCapability{
			api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}},
		},
	})
	if err != nil {
		t.Fatalf("service descriptor: %v", err)
	}
	validator := New(Config{Catalog: catalog, Protocol: protocolDescriptor, Service: serviceDescriptor})
	_, err = validator.AdmitLLMFrame(llmFrame(validAdmissionRequest("fixture", api.ModeUnary)))
	if Code(err) != api.ErrorUnsupported {
		t.Fatalf("service protocol mismatch must be unsupported: %T %v", err, err)
	}
}

func TestAdmitRejectsModelOutsideExactServiceDeployment(t *testing.T) {
	catalog, err := model.NewCatalogFromRecords([]ports.ModelRecord{fixtureRecord("fixture", ports.ModelCapability{
		Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText},
		OutputModalities: []api.Modality{api.ModalityText},
	})})
	if err != nil {
		t.Fatalf("catalog: %v", err)
	}
	serviceDescriptor, err := service.NewDescriptor(service.DescriptorConfig{
		Kind: "service.test", Protocols: []string{"fixture.protocol"}, Models: []string{"other-model"},
		Operations: map[api.Operation]service.OperationCapability{
			api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}},
		},
	})
	if err != nil {
		t.Fatalf("service descriptor: %v", err)
	}
	validator := New(Config{Catalog: catalog, Protocol: testkit.ProtocolCapabilities(), Service: serviceDescriptor})
	_, err = validator.AdmitLLMFrame(llmFrame(validAdmissionRequest("fixture", api.ModeUnary)))
	if Code(err) != api.ErrorUnsupported {
		t.Fatalf("undeployable exact model must be unsupported: %T %v", err, err)
	}
}

func TestAdmitReducesStructuralFeaturesBeforeCapabilityIntersection(t *testing.T) {
	validator := testValidator(t, fixtureRecord("fixture", ports.ModelCapability{
		Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText},
		OutputModalities: []api.Modality{api.ModalityText},
	}))
	tests := []struct {
		name   string
		mutate func(*api.LLMRequest)
		code   api.ErrorCode
	}{
		{name: "tools infer feature", mutate: func(request *api.LLMRequest) {
			request.Input.Tools = []api.ToolDefinition{{Name: "lookup", InputSchema: []byte(`{}`)}}
		}, code: api.ErrorUnsupported},
		{name: "structured output infers feature", mutate: func(request *api.LLMRequest) {
			request.Input.ResponseFormat = &api.ResponseFormat{Type: "json_schema", Schema: []byte(`{}`)}
		}, code: api.ErrorUnsupported},
		{name: "orphan tool feature", mutate: func(request *api.LLMRequest) {
			request.Features = []api.Feature{api.FeatureToolCalls}
		}, code: api.ErrorUnsupported},
		{name: "orphan structured feature", mutate: func(request *api.LLMRequest) {
			request.Features = []api.Feature{api.FeatureStructured}
		}, code: api.ErrorUnsupported},
		{name: "matched tool requirement reaches model intersection", mutate: func(request *api.LLMRequest) {
			request.Input.Tools = []api.ToolDefinition{{Name: "lookup", InputSchema: []byte(`{}`)}}
			request.Features = []api.Feature{api.FeatureToolCalls}
		}, code: api.ErrorUnsupported},
		{name: "matched structured requirement reaches model intersection", mutate: func(request *api.LLMRequest) {
			request.Input.ResponseFormat = &api.ResponseFormat{Type: "json_schema", Schema: []byte(`{}`)}
			request.Features = []api.Feature{api.FeatureStructured}
		}, code: api.ErrorUnsupported},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			request := validAdmissionRequest("fixture", api.ModeUnary)
			testCase.mutate(&request)
			_, err := validator.AdmitLLMFrame(llmFrame(request))
			if Code(err) != testCase.code {
				t.Fatalf("semantic requirement returned %s, want %s: %T %v", Code(err), testCase.code, err, err)
			}
		})
	}
}

func TestAdmitUsesMessageContentModalitiesInsteadOfTopLevelMirror(t *testing.T) {
	validator := testValidator(t, fixtureRecord("fixture", ports.ModelCapability{
		Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText},
		OutputModalities: []api.Modality{api.ModalityText},
	}))
	request := validAdmissionRequest("fixture", api.ModeUnary)
	request.Input.Messages[0].Content = append(request.Input.Messages[0].Content, api.ContentPart{
		Type: "image",
		Artifact: &api.ArtifactRef{
			ArtifactID: "artifact-1", Revision: 1, Representation: "original", Kind: "image",
			MIMEType: "image/png", ContentRef: "content-1", Digest: "sha256:" + strings.Repeat("0", 64), Size: 1,
		},
	})
	_, err := validator.AdmitLLMFrame(llmFrame(request))
	if Code(err) != api.ErrorUnsupported {
		t.Fatalf("image message admitted by a text-only model: %T %v", err, err)
	}
}

func TestRequirementsReducerCoversPromptInstructionsAndArtifacts(t *testing.T) {
	t.Run("system prompt is text input", func(t *testing.T) {
		validator := testValidator(t, fixtureRecord("image-only-llm", ports.ModelCapability{
			Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityImage}, OutputModalities: []api.Modality{api.ModalityText},
		}))
		request := validAdmissionRequest("image-only-llm", api.ModeUnary)
		request.Input.Messages[0].Content = []api.ContentPart{{Type: "image", Artifact: testArtifact(api.ArtifactKindImage)}}
		request.Input.SystemPrompt = "follow this policy"
		if _, err := validator.AdmitLLMFrame(llmFrame(request)); Code(err) != api.ErrorUnsupported {
			t.Fatalf("text system prompt bypassed image-only model: %T %v", err, err)
		}
	})

	t.Run("realtime instructions are text input", func(t *testing.T) {
		validator := testValidator(t, fixtureRecord("audio-only-realtime", ports.ModelCapability{
			Operation: api.OperationRealtime, InputModalities: []api.Modality{api.ModalityAudio}, OutputModalities: []api.Modality{api.ModalityAudio},
		}))
		request := api.LLMRequest{
			Kind: api.RequestKindLLM, SchemaVersion: 1, OperationID: "realtime-instructions", BaseModel: "audio-only-realtime",
			Operation: operationPtr(api.OperationRealtime), Mode: api.ModeDuplex,
			Input: api.LLMInput{Kind: "realtime", Instructions: "speak clearly"},
		}
		if _, err := validator.AdmitLLMFrame(llmFrame(request)); Code(err) != api.ErrorUnsupported {
			t.Fatalf("text realtime instructions bypassed audio-only model: %T %v", err, err)
		}
	})

	t.Run("realtime content fields are not ignored", func(t *testing.T) {
		validator := testValidator(t, fixtureRecord("audio-only-realtime-content", ports.ModelCapability{
			Operation: api.OperationRealtime, InputModalities: []api.Modality{api.ModalityAudio}, OutputModalities: []api.Modality{api.ModalityAudio},
		}))
		request := api.LLMRequest{
			Kind: api.RequestKindLLM, SchemaVersion: 1, OperationID: "realtime-content", BaseModel: "audio-only-realtime-content",
			Operation: operationPtr(api.OperationRealtime), Mode: api.ModeDuplex,
			Input: api.LLMInput{Kind: "realtime", Messages: []api.Message{{Role: "user", Content: []api.ContentPart{{Type: "text", Text: "continue"}}}}},
		}
		if _, err := validator.AdmitLLMFrame(llmFrame(request)); Code(err) != api.ErrorUnsupported {
			t.Fatalf("text realtime content bypassed audio-only model: %T %v", err, err)
		}
	})

	t.Run("media prompt contributes text modality", func(t *testing.T) {
		validator := testValidator(t, fixtureRecord("image-only-generator", ports.ModelCapability{
			Operation: api.OperationImageGeneration, InputModalities: []api.Modality{api.ModalityImage}, OutputModalities: []api.Modality{api.ModalityImage},
		}))
		request := api.MediaRequest{
			Kind: api.RequestKindMedia, SchemaVersion: 1, OperationID: "image-source", BaseModel: "image-only-generator",
			Operation: operationPtr(api.OperationImageGeneration), Mode: api.ModeUnary,
			Input: api.MediaInput{Kind: "image_generation", Prompt: "edit this", Source: testArtifact(api.ArtifactKindImage)},
		}
		if _, err := validator.AdmitMediaFrame(mediaFrame(request)); Code(err) != api.ErrorUnsupported {
			t.Fatalf("text prompt bypassed image-only source model: %T %v", err, err)
		}
	})

	t.Run("media source contributes artifact modality", func(t *testing.T) {
		validator := testValidator(t, fixtureRecord("text-only-image-generator", ports.ModelCapability{
			Operation: api.OperationImageGeneration, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityImage},
		}))
		request := api.MediaRequest{
			Kind: api.RequestKindMedia, SchemaVersion: 1, OperationID: "image-source-modality", BaseModel: "text-only-image-generator",
			Operation: operationPtr(api.OperationImageGeneration), Mode: api.ModeUnary,
			Input: api.MediaInput{Kind: "image_generation", Prompt: "edit this", Source: testArtifact(api.ArtifactKindImage)},
		}
		if _, err := validator.AdmitMediaFrame(mediaFrame(request)); Code(err) != api.ErrorUnsupported {
			t.Fatalf("image source bypassed text-only model: %T %v", err, err)
		}
	})

	t.Run("transcription artifact contributes modality", func(t *testing.T) {
		validator := testValidator(t, fixtureRecord("audio-only-transcriber", ports.ModelCapability{
			Operation: api.OperationAudioTranscription, InputModalities: []api.Modality{api.ModalityAudio}, OutputModalities: []api.Modality{api.ModalityText},
		}))
		request := api.MediaRequest{
			Kind: api.RequestKindMedia, SchemaVersion: 1, OperationID: "video-transcription", BaseModel: "audio-only-transcriber",
			Operation: operationPtr(api.OperationAudioTranscription), Mode: api.ModeUnary,
			Input: api.MediaInput{Kind: "audio_transcription", Media: testArtifact(api.ArtifactKindVideo)},
		}
		if _, err := validator.AdmitMediaFrame(mediaFrame(request)); Code(err) != api.ErrorUnsupported {
			t.Fatalf("video artifact bypassed audio-only transcriber: %T %v", err, err)
		}
	})

	t.Run("unknown artifact kind is rejected by the canonical mapping", func(t *testing.T) {
		validator := testValidator(t, fixtureRecord("image-generator-unknown-artifact", ports.ModelCapability{
			Operation: api.OperationImageGeneration, InputModalities: []api.Modality{api.ModalityText, api.ModalityImage}, OutputModalities: []api.Modality{api.ModalityImage},
		}))
		request := api.MediaRequest{
			Kind: api.RequestKindMedia, SchemaVersion: 1, OperationID: "unknown-artifact", BaseModel: "image-generator-unknown-artifact",
			Operation: operationPtr(api.OperationImageGeneration), Mode: api.ModeUnary,
			Input: api.MediaInput{Kind: "image_generation", Prompt: "edit", Source: testArtifact("provider-specific")},
		}
		if _, err := validator.AdmitMediaFrame(mediaFrame(request)); Code(err) != api.ErrorInvalidRequest {
			t.Fatalf("unknown artifact kind was not rejected at admission: %T %v", err, err)
		}
	})
}

func testArtifact(kind string) *api.ArtifactRef {
	return &api.ArtifactRef{ArtifactID: "artifact", Revision: 1, Representation: "original", Kind: kind, MIMEType: kind + "/octet-stream", ContentRef: "content", Digest: "sha256:" + strings.Repeat("0", 64)}
}

func TestReasoningSyntaxPrecedesCapabilityIntersection(t *testing.T) {
	catalog, err := model.NewCatalogFromRecords([]ports.ModelRecord{fixtureRecord("fixture", ports.ModelCapability{
		Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText},
		OutputModalities: []api.Modality{api.ModalityText},
	})})
	if err != nil {
		t.Fatalf("catalog: %v", err)
	}
	narrow, err := protocol.NewDescriptor("protocol.narrow", map[api.Operation]protocol.OperationCapability{
		api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeServerStream}},
	})
	if err != nil {
		t.Fatalf("protocol descriptor: %v", err)
	}
	serviceDescriptor, err := service.NewDescriptor(service.DescriptorConfig{
		Kind: "service.test", Protocols: []string{"protocol.narrow"}, Models: []string{"fixture"},
		Operations: map[api.Operation]service.OperationCapability{
			api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeServerStream}},
		},
	})
	if err != nil {
		t.Fatalf("service descriptor: %v", err)
	}
	validator := New(Config{Catalog: catalog, Protocol: narrow, Service: serviceDescriptor})
	request := validAdmissionRequest("fixture", api.ModeUnary)
	request.Input.Reasoning = &api.ReasoningConfig{Thinking: "unknown"}
	_, err = validator.AdmitLLMFrame(llmFrame(request))
	if Code(err) != api.ErrorInvalidRequest {
		t.Fatalf("reasoning syntax was masked by capability intersection: %T %v", err, err)
	}
}

func TestAdmitRejectsReasoningOutsideProtocolIntersection(t *testing.T) {
	thinking := api.ThinkingAdaptive
	effort := api.ReasoningEffortHigh
	catalog, err := model.NewCatalogFromRecords([]ports.ModelRecord{fixtureRecord("reasoning", ports.ModelCapability{
		Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText},
		OutputModalities: []api.Modality{api.ModalityText}, Reasoning: &ports.ModelReasoning{
			ThinkingModes: []ports.ModelThinkingMode{{Thinking: thinking, Efforts: []api.ReasoningEffort{effort}}},
		},
	})})
	if err != nil {
		t.Fatalf("catalog: %v", err)
	}
	narrow, err := protocol.NewDescriptor("protocol.narrow", map[api.Operation]protocol.OperationCapability{
		api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []protocol.ReasoningCapability{{Thinking: api.ThinkingDisabled}}},
	})
	if err != nil {
		t.Fatalf("protocol descriptor: %v", err)
	}
	validator := New(Config{Catalog: catalog, Protocol: narrow, Service: testkit.ServiceCapabilities("reasoning")})
	request := validAdmissionRequest("reasoning", api.ModeUnary)
	request.Input.Reasoning = &api.ReasoningConfig{Thinking: thinking, Effort: effort}
	_, err = validator.AdmitLLMFrame(llmFrame(request))
	if Code(err) != api.ErrorUnsupported {
		t.Fatalf("protocol reasoning intersection must be unsupported: %T %v", err, err)
	}
}

func TestAdmitClassifiesAmbiguousEffortOnlyReasoningAsInvalidRequest(t *testing.T) {
	validator := testValidator(t, fixtureRecord("ambiguous-reasoning", ports.ModelCapability{
		Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText},
		OutputModalities: []api.Modality{api.ModalityText}, Reasoning: &ports.ModelReasoning{
			ThinkingModes: []ports.ModelThinkingMode{
				{Thinking: api.ThinkingEnabled, Efforts: []api.ReasoningEffort{api.ReasoningEffortLow}},
				{Thinking: api.ThinkingAdaptive, Efforts: []api.ReasoningEffort{api.ReasoningEffortLow}},
			},
		},
	}))
	request := validAdmissionRequest("ambiguous-reasoning", api.ModeUnary)
	request.Input.Reasoning = &api.ReasoningConfig{Effort: api.ReasoningEffortLow}
	_, err := validator.AdmitLLMFrame(llmFrame(request))
	if Code(err) != api.ErrorInvalidRequest {
		t.Fatalf("ambiguous effort-only reasoning returned %q: %T %v", Code(err), err, err)
	}
}

func TestAdmitMediaDefaultsExactModelOperation(t *testing.T) {
	validator := testValidator(t, fixtureRecord("image-model", ports.ModelCapability{
		Operation:        api.OperationImageGeneration,
		InputModalities:  []api.Modality{api.ModalityText},
		OutputModalities: []api.Modality{api.ModalityImage},
	}))
	request := api.MediaRequest{
		Kind:          api.RequestKindMedia,
		SchemaVersion: 1,
		OperationID:   "media-1",
		BaseModel:     "image-model",
		Modality:      api.ModalityImage,
		Mode:          api.ModeUnary,
		Input:         api.MediaInput{Kind: "image_generation", Prompt: "a mote"},
		Features:      []api.Feature{},
	}

	admitted, err := validator.AdmitMediaFrame(mediaFrame(request))
	if err != nil {
		t.Fatalf("admit media request: %v", err)
	}
	if normalized := admitted.Request(); normalized.Operation == nil || *normalized.Operation != api.OperationImageGeneration || normalized.BaseModel != request.BaseModel {
		t.Fatalf("media request was not normalized from the exact model: %+v", admitted)
	}
	if admitted.ProtocolID() == "" || admitted.ServiceKind() == "" {
		t.Fatal("admitted media request did not freeze protocol and service identities")
	}
}

func TestAdmitMediaRejectsUnsupportedTextInput(t *testing.T) {
	validator := testValidator(t, fixtureRecord("image-source-only", ports.ModelCapability{
		Operation:        api.OperationImageGeneration,
		InputModalities:  []api.Modality{api.ModalityImage},
		OutputModalities: []api.Modality{api.ModalityImage},
	}))
	request := api.MediaRequest{
		Kind: api.RequestKindMedia, SchemaVersion: 1, OperationID: "media-input",
		BaseModel: "image-source-only", Operation: operationPtr(api.OperationImageGeneration),
		Modality: api.ModalityImage, Mode: api.ModeUnary,
		Input:    api.MediaInput{Kind: "image_generation", Prompt: "a mote"},
		Features: []api.Feature{},
	}
	_, err := validator.AdmitMediaFrame(mediaFrame(request))
	if Code(err) != api.ErrorUnsupported {
		t.Fatalf("unsupported media input modality must be rejected as capability error: %T %v", err, err)
	}
}

func TestAdmitResolvesGenerationParametersBeforeFreezingRequest(t *testing.T) {
	temperatureDefault := 0.7
	temperatureMinimum := 0.2
	temperatureMaximum := 1.5
	topPMinimum := 0.25
	policy := &ports.ModelGeneration{
		Temperature: &ports.ModelNumericFloat{
			Minimum: &temperatureMinimum,
			Maximum: &temperatureMaximum,
			Default: &temperatureDefault,
		},
		TopP:            &ports.ModelNumericFloat{Minimum: &topPMinimum},
		MaxOutputTokens: &ports.ModelOutputTokens{},
		Stop:            &ports.ModelStop{Default: []string{"\n\n"}},
	}
	validator := testValidator(t, ports.ModelRecord{
		BaseModel: "generation-controls",
		Lifecycle: ports.ModelLifecycleActive,
		TokenLimits: ports.ModelTokenLimits{
			MinOutputTokens: 256,
			MaxOutputTokens: 1024,
		},
		Capability: ports.ModelCapability{
			Operation:        api.OperationGenerate,
			InputModalities:  []api.Modality{api.ModalityText},
			OutputModalities: []api.Modality{api.ModalityText},
			Generation:       policy,
		},
	})
	requestedTemperature := 4.0
	requestedTopP := 0.1
	requestedMaxOutput := int64(128)
	seed := int64(7)
	request := validAdmissionRequest("generation-controls", api.ModeUnary)
	request.Input.Parameters = api.GenerationParameters{
		Temperature:     &requestedTemperature,
		TopP:            &requestedTopP,
		MaxOutputTokens: &requestedMaxOutput,
		Stop:            []string{"END"},
		Seed:            &seed,
	}
	admitted, err := validator.AdmitLLMFrame(llmFrame(request))
	if err != nil {
		t.Fatalf("admit generation controls: %v", err)
	}
	resolved := admitted.Request().Input.Parameters
	if resolved.Temperature == nil || *resolved.Temperature != temperatureMaximum {
		t.Fatalf("temperature was not clamped by admission: %+v", resolved.Temperature)
	}
	if resolved.TopP == nil || *resolved.TopP != topPMinimum {
		t.Fatalf("top_p was not clamped by admission: %+v", resolved.TopP)
	}
	if resolved.MaxOutputTokens == nil || *resolved.MaxOutputTokens != 256 {
		t.Fatalf("max_output_tokens was not clamped by admission: %+v", resolved.MaxOutputTokens)
	}
	if len(resolved.Stop) != 1 || resolved.Stop[0] != "END" {
		t.Fatalf("stop was not retained by admission: %+v", resolved.Stop)
	}
	if resolved.Seed != nil {
		t.Fatalf("unsupported seed was not filtered by admission: %v", *resolved.Seed)
	}

	media := fixtureRecord("generation-media-controls", ports.ModelCapability{
		Operation:        api.OperationImageGeneration,
		InputModalities:  []api.Modality{api.ModalityText},
		OutputModalities: []api.Modality{api.ModalityImage},
	})
	mediaValidator := testValidator(t, media)
	mediaRequest := api.MediaRequest{
		Kind: api.RequestKindMedia, SchemaVersion: 1, OperationID: "media-controls",
		BaseModel: "generation-media-controls", Modality: api.ModalityImage, Mode: api.ModeUnary,
		Input:    api.MediaInput{Kind: "image_generation", Prompt: "a mote", Parameters: api.GenerationParameters{Temperature: &requestedTemperature}},
		Features: []api.Feature{},
	}
	mediaAdmitted, err := mediaValidator.AdmitMediaFrame(mediaFrame(mediaRequest))
	if err != nil {
		t.Fatalf("admit media generation controls: %v", err)
	}
	mediaParameters := mediaAdmitted.Request().Input.Parameters
	if mediaParameters.Temperature != nil || mediaParameters.TopP != nil || mediaParameters.MaxOutputTokens != nil || len(mediaParameters.Stop) != 0 || mediaParameters.Seed != nil {
		t.Fatalf("media did not use the shared generation resolver to filter unsupported controls: %+v", mediaParameters)
	}
}

func TestAdmitResolvesDeclaredGenerationParametersForMedia(t *testing.T) {
	minimum := 0.1
	maximum := 1.0
	limit := int64(512)
	requestedTemperature := 2.0
	requestedTokens := int64(1024)
	validator := testValidator(t, ports.ModelRecord{
		BaseModel: "media-generation-controls",
		Lifecycle: ports.ModelLifecycleActive,
		TokenLimits: ports.ModelTokenLimits{
			MaxOutputTokens: limit,
		},
		Capability: ports.ModelCapability{
			Operation:        api.OperationImageGeneration,
			InputModalities:  []api.Modality{api.ModalityText},
			OutputModalities: []api.Modality{api.ModalityImage},
			Generation: &ports.ModelGeneration{
				Temperature:     &ports.ModelNumericFloat{Minimum: &minimum, Maximum: &maximum},
				MaxOutputTokens: &ports.ModelOutputTokens{},
			},
		},
	})
	request := api.MediaRequest{
		Kind: api.RequestKindMedia, SchemaVersion: 1, OperationID: "media-generation-controls",
		BaseModel: "media-generation-controls", Modality: api.ModalityImage, Mode: api.ModeUnary,
		Input: api.MediaInput{
			Kind: "image_generation", Prompt: "a mote",
			Parameters: api.GenerationParameters{Temperature: &requestedTemperature, MaxOutputTokens: &requestedTokens},
		},
		Features: []api.Feature{},
	}
	admitted, err := validator.AdmitMediaFrame(mediaFrame(request))
	if err != nil {
		t.Fatalf("admit media generation controls: %v", err)
	}
	resolved := admitted.Request().Input.Parameters
	if resolved.Temperature == nil || *resolved.Temperature != maximum {
		t.Fatalf("media temperature was not clamped by admission: %+v", resolved.Temperature)
	}
	if resolved.MaxOutputTokens == nil || *resolved.MaxOutputTokens != limit {
		t.Fatalf("media max_output_tokens was not clamped by admission: %+v", resolved.MaxOutputTokens)
	}
}

func TestValidatorReadsTheLatestCatalogStoreForEachAdmission(t *testing.T) {
	source := &catalogRefreshSource{records: []ports.ModelRecord{catalogRecord("first")}}
	store, err := model.NewCatalogStore(context.Background(), source)
	if err != nil {
		t.Fatalf("catalog store: %v", err)
	}
	validator := New(Config{Catalog: store, Protocol: testkit.ProtocolCapabilities(), Service: testkit.ServiceCapabilities("first", "second")})
	request := validAdmissionRequest("first", api.ModeUnary)
	if _, err := validator.AdmitLLMFrame(llmFrame(request)); err != nil {
		t.Fatalf("first catalog admission: %v", err)
	}

	source.records = []ports.ModelRecord{catalogRecord("second")}
	if err := store.Refresh(context.Background()); err != nil {
		t.Fatalf("refresh catalog: %v", err)
	}
	request.BaseModel = "second"
	if _, err := validator.AdmitLLMFrame(llmFrame(request)); err != nil {
		t.Fatalf("admission did not read refreshed catalog: %v", err)
	}
}

func testValidator(t *testing.T, records ...ports.ModelRecord) Validator {
	t.Helper()
	catalog, err := model.NewCatalogFromRecords(records)
	if err != nil {
		t.Fatalf("catalog: %v", err)
	}
	models := make([]string, 0, len(records))
	for _, record := range records {
		models = append(models, record.BaseModel)
	}
	return New(Config{Catalog: catalog, Protocol: testkit.ProtocolCapabilities(), Service: testkit.ServiceCapabilities(models...)})
}

func validAdmissionRequest(baseModel string, mode api.DeliveryMode) api.LLMRequest {
	return api.LLMRequest{
		Kind: api.RequestKindLLM, SchemaVersion: 1, OperationID: "op", BaseModel: baseModel,
		Operation: operationPtr(api.OperationGenerate), Modality: api.ModalityText, Mode: mode,
		Input:    api.LLMInput{Kind: "generate", Messages: []api.Message{{Role: "user", Content: []api.ContentPart{{Type: "text", Text: "hi"}}}}},
		Features: []api.Feature{},
	}
}

func llmFrame(request api.LLMRequest) api.LLMRequestFrame {
	return api.LLMRequestFrame{Request: request}
}

func mediaFrame(request api.MediaRequest) api.MediaRequestFrame {
	return api.MediaRequestFrame{Request: request}
}

func operationPtr(operation api.Operation) *api.Operation { return &operation }

type catalogRefreshSource struct {
	records []ports.ModelRecord
}

func (source *catalogRefreshSource) LoadModelCatalog(context.Context) ([]ports.ModelRecord, error) {
	return source.records, nil
}

func catalogRecord(baseModel string) ports.ModelRecord {
	return fixtureRecord(baseModel, ports.ModelCapability{
		Operation:        api.OperationGenerate,
		InputModalities:  []api.Modality{api.ModalityText},
		OutputModalities: []api.Modality{api.ModalityText},
	})
}

func fixtureRecord(baseModel string, capability ports.ModelCapability) ports.ModelRecord {
	return ports.ModelRecord{
		BaseModel:  baseModel,
		Lifecycle:  ports.ModelLifecycleActive,
		Capability: capability,
	}
}
