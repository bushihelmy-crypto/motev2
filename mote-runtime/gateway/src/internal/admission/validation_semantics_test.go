package admission

import (
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

func TestEnvelopeAndAdmissionErrorBoundaries(t *testing.T) {
	tests := []struct {
		name          string
		kind          api.RequestKind
		expectedKind  api.RequestKind
		schemaVersion int
		operationID   string
		baseModel     string
		want          string
	}{
		{name: "kind", kind: api.RequestKindMedia, expectedKind: api.RequestKindLLM, schemaVersion: 1, operationID: "op", baseModel: "model", want: "request kind"},
		{name: "schema", kind: api.RequestKindLLM, expectedKind: api.RequestKindLLM, schemaVersion: 2, operationID: "op", baseModel: "model", want: "schema_version"},
		{name: "operation id", kind: api.RequestKindLLM, expectedKind: api.RequestKindLLM, schemaVersion: 1, operationID: " ", baseModel: "model", want: "operation_id"},
		{name: "base model", kind: api.RequestKindLLM, expectedKind: api.RequestKindLLM, schemaVersion: 1, operationID: "op", baseModel: "bad/model", want: "base_model"},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			err := validateEnvelope(testCase.kind, testCase.expectedKind, testCase.schemaVersion, testCase.operationID, testCase.baseModel)
			if err == nil || !strings.Contains(err.Error(), testCase.want) {
				t.Fatalf("expected %q envelope error, got %v", testCase.want, err)
			}
		})
	}

	if ensureAdmissionError(nil) != nil {
		t.Fatal("nil error was changed")
	}
	plain := errors.New("plain")
	wrapped := ensureAdmissionError(plain)
	if Code(wrapped) != api.ErrorInvalidRequest || !errors.Is(wrapped, plain) {
		t.Fatalf("plain validation error was not projected: %T %v", wrapped, wrapped)
	}
	existing := unsupported(errors.New("unsupported"))
	if ensureAdmissionError(existing) != existing {
		t.Fatal("existing admission error identity changed")
	}
	if Code(plain) != "" {
		t.Fatal("non-admission error acquired an admission code")
	}
	if admissionError, ok := AsAdmission(plain); ok || admissionError != nil {
		t.Fatalf("non-admission error was classified: %+v", admissionError)
	}
	if operationValue(nil) != "" || mediaOperationKind(api.OperationEmbedding) != "" {
		t.Fatal("unknown operation helpers invented a value")
	}
}

func TestLLMShapeOwnsProfileModeKindAndModalityFailures(t *testing.T) {
	capability := admissionCapability(t, ports.ModelRecord{
		BaseModel: "shape-llm", Lifecycle: ports.ModelLifecycleActive,
		Capability: ports.ModelCapability{
			Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityText},
		},
	})
	generate := api.OperationGenerate
	valid := api.LLMRequest{
		Operation: &generate, Mode: api.ModeUnary,
		Input: api.LLMInput{Kind: "generate", Messages: []api.Message{{Content: []api.ContentPart{{Type: api.ArtifactKindText, Text: "hello"}}}}},
	}
	tests := []struct {
		name     string
		request  api.LLMRequest
		required requirements
		want     string
	}{
		{name: "outside profile", request: api.LLMRequest{}, want: "outside kernel_llm"},
		{name: "generate mode", request: func() api.LLMRequest { value := valid; value.Mode = api.ModeDuplex; return value }(), want: "unary/server_stream"},
		{name: "generate kind", request: func() api.LLMRequest { value := valid; value.Input.Messages = nil; return value }(), want: "kind=generate and messages"},
		{name: "output modality", request: valid, required: requirements{outputModalities: []api.Modality{api.ModalityAudio}}, want: "does not produce modality"},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			err := validateLLMShape(testCase.request, testCase.required, capability)
			if err == nil || !strings.Contains(err.Error(), testCase.want) {
				t.Fatalf("expected %q, got %v", testCase.want, err)
			}
		})
	}

	realtime := api.OperationRealtime
	if err := validateRealtimeShape(api.LLMRequest{Operation: &realtime, Mode: api.ModeUnary, Input: api.LLMInput{Kind: "generate"}}); err == nil {
		t.Fatal("invalid realtime mode/kind was accepted")
	}
	embedding := api.OperationEmbedding
	if err := validateLLMOperationShape(api.LLMRequest{Operation: &embedding}); err != nil {
		t.Fatalf("operation without a current LLM-specific shape gained one: %v", err)
	}
}

func TestMediaShapeOwnsEveryOperationSpecificInvariant(t *testing.T) {
	capability := admissionCapability(t, ports.ModelRecord{
		BaseModel: "shape-media", Lifecycle: ports.ModelLifecycleActive,
		Capability: ports.ModelCapability{
			Operation: api.OperationImageGeneration, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityImage},
		},
	})
	image := api.OperationImageGeneration
	valid := api.MediaRequest{Operation: &image, Mode: api.ModeUnary, Input: api.MediaInput{Kind: "image_generation", Prompt: "draw"}}
	tests := []struct {
		name     string
		request  api.MediaRequest
		required requirements
		want     string
	}{
		{name: "outside profile", request: api.MediaRequest{}, want: "outside execution_media"},
		{name: "mode", request: func() api.MediaRequest { value := valid; value.Mode = api.ModeDuplex; return value }(), want: "does not support mode"},
		{name: "kind", request: func() api.MediaRequest { value := valid; value.Input.Kind = "video_generation"; return value }(), want: "operation/input kind"},
		{name: "prompt", request: func() api.MediaRequest { value := valid; value.Input.Prompt = " "; return value }(), want: "prompt is required"},
		{name: "output modality", request: valid, required: requirements{outputModalities: []api.Modality{api.ModalityAudio}}, want: "does not produce modality"},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			err := validateMediaShape(testCase.request, testCase.required, capability)
			if err == nil || !strings.Contains(err.Error(), testCase.want) {
				t.Fatalf("expected %q, got %v", testCase.want, err)
			}
		})
	}

	audio := api.OperationAudioGeneration
	if err := validateMediaText(api.MediaRequest{Operation: &audio, Input: api.MediaInput{Text: "hello"}}, mediaShapes[audio]); err == nil {
		t.Fatal("audio generation without voice was accepted")
	}
	if err := validateMediaArtifacts(api.MediaRequest{Operation: &audio, Input: api.MediaInput{Source: &api.ArtifactRef{Kind: api.ArtifactKindAudio}}}, mediaShapes[audio]); err == nil {
		t.Fatal("source artifact was accepted by an operation that forbids it")
	}
	if err := validateMediaArtifacts(api.MediaRequest{Operation: &image, Input: api.MediaInput{Media: &api.ArtifactRef{Kind: api.ArtifactKindAudio}}}, mediaShapes[image]); err == nil {
		t.Fatal("media artifact was accepted by an operation that forbids it")
	}
	transcription := api.OperationAudioTranscription
	if err := validateMediaArtifacts(api.MediaRequest{Operation: &transcription}, mediaShapes[transcription]); err == nil {
		t.Fatal("transcription without a media artifact was accepted")
	}
	embedding := api.OperationEmbedding
	if err := validateMediaInput(api.MediaRequest{Operation: &embedding}); err == nil {
		t.Fatal("operation without a media input rule was accepted")
	}
}

func TestCapabilityIntersectionReportsTheExactRejectingOwner(t *testing.T) {
	capability := admissionCapability(t, ports.ModelRecord{
		BaseModel: "feature-model", Lifecycle: ports.ModelLifecycleActive,
		Capability: ports.ModelCapability{
			Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityText},
			Features: []api.Feature{api.FeatureToolCalls, api.FeatureStructured},
		},
	})
	noFeaturesProtocol, err := protocol.NewDescriptor("protocol.no-features", map[api.Operation]protocol.OperationCapability{
		api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}},
	})
	if err != nil {
		t.Fatal(err)
	}
	noFeaturesService, err := service.NewDescriptor(service.DescriptorConfig{
		Kind: "service.no-features", Protocols: []string{"fixture.protocol"}, Models: []string{"feature-model"},
		Operations: map[api.Operation]service.OperationCapability{api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}}},
	})
	if err != nil {
		t.Fatal(err)
	}

	validator := Validator{protocol: noFeaturesProtocol, service: testkit.ServiceCapabilities("feature-model")}
	if err := validator.validateFeatureIntersection(api.OperationGenerate, []api.Feature{api.FeatureToolCalls}, capability); Code(err) != api.ErrorUnsupported || !strings.Contains(err.Error(), "protocol") {
		t.Fatalf("protocol feature rejection changed: %T %v", err, err)
	}
	validator = Validator{protocol: testkit.ProtocolCapabilities(), service: noFeaturesService}
	if err := validator.validateFeatureIntersection(api.OperationGenerate, []api.Feature{api.FeatureToolCalls}, capability); Code(err) != api.ErrorUnsupported || !strings.Contains(err.Error(), "service") {
		t.Fatalf("service feature rejection changed: %T %v", err, err)
	}
	if err := validateModelFeature(api.FeatureToolCalls, capability); err != nil {
		t.Fatalf("declared model feature was rejected: %v", err)
	}
	if err := validateModelFeature(api.FeatureUsage, capability); err != nil {
		t.Fatalf("service/protocol-only feature was rejected by model owner: %v", err)
	}

	wrongOperationProtocol, err := protocol.NewDescriptor("protocol.realtime-only", map[api.Operation]protocol.OperationCapability{
		api.OperationRealtime: {Modes: []api.DeliveryMode{api.ModeDuplex}},
	})
	if err != nil {
		t.Fatal(err)
	}
	serviceForWrongOperation, err := service.NewDescriptor(service.DescriptorConfig{
		Kind: "service.operation", Protocols: []string{"protocol.realtime-only"}, Models: []string{"feature-model"},
		Operations: map[api.Operation]service.OperationCapability{api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}}},
	})
	if err != nil {
		t.Fatal(err)
	}
	validator = Validator{protocol: wrongOperationProtocol, service: serviceForWrongOperation}
	if err := validator.validateCapabilities("feature-model", api.OperationGenerate, api.ModeUnary, nil, nil, capability); Code(err) != api.ErrorUnsupported || !strings.Contains(err.Error(), "protocol") {
		t.Fatalf("protocol operation rejection changed: %T %v", err, err)
	}
}

func TestCapabilityIntersectionRejectsReasoningAtProtocolAndServiceOwners(t *testing.T) {
	adaptive := api.ThinkingAdaptive
	high := api.ReasoningEffortHigh
	capability := admissionCapability(t, ports.ModelRecord{
		BaseModel: "reasoning-owner", Lifecycle: ports.ModelLifecycleActive,
		Capability: ports.ModelCapability{
			Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityText},
			Reasoning: &ports.ModelReasoning{ThinkingModes: []ports.ModelThinkingMode{{Thinking: adaptive, Efforts: []api.ReasoningEffort{high}}}},
		},
	})
	reasoning := &api.ReasoningConfig{Thinking: adaptive, Effort: high}
	disabledProtocol, err := protocol.NewDescriptor("protocol.disabled", map[api.Operation]protocol.OperationCapability{
		api.OperationGenerate: {Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []protocol.ReasoningCapability{{Thinking: api.ThinkingDisabled}}},
	})
	if err != nil {
		t.Fatal(err)
	}
	serviceForDisabledProtocol, err := service.NewDescriptor(service.DescriptorConfig{
		Kind: "service.reasoning", Protocols: []string{"protocol.disabled"}, Models: []string{"reasoning-owner"},
		Operations: map[api.Operation]service.OperationCapability{api.OperationGenerate: {
			Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []service.ReasoningCapability{{Thinking: adaptive, Efforts: []api.ReasoningEffort{high}}},
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	validator := Validator{protocol: disabledProtocol, service: serviceForDisabledProtocol}
	if err := validator.validateCapabilities("reasoning-owner", api.OperationGenerate, api.ModeUnary, nil, reasoning, capability); Code(err) != api.ErrorUnsupported || !strings.Contains(err.Error(), "protocol") {
		t.Fatalf("protocol reasoning rejection changed: %T %v", err, err)
	}

	disabledService, err := service.NewDescriptor(service.DescriptorConfig{
		Kind: "service.disabled", Protocols: []string{"fixture.protocol"}, Models: []string{"reasoning-owner"},
		Operations: map[api.Operation]service.OperationCapability{api.OperationGenerate: {
			Modes: []api.DeliveryMode{api.ModeUnary}, Reasoning: []service.ReasoningCapability{{Thinking: api.ThinkingDisabled}},
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	validator = Validator{protocol: testkit.ProtocolCapabilities(), service: disabledService}
	if err := validator.validateCapabilities("reasoning-owner", api.OperationGenerate, api.ModeUnary, nil, reasoning, capability); Code(err) != api.ErrorUnsupported || !strings.Contains(err.Error(), "service") {
		t.Fatalf("service reasoning rejection changed: %T %v", err, err)
	}
}

func TestAdmitMediaRejectsUnknownModelAndCapabilityIntersection(t *testing.T) {
	record := ports.ModelRecord{
		BaseModel: "media-owner", Lifecycle: ports.ModelLifecycleActive,
		Capability: ports.ModelCapability{
			Operation: api.OperationImageGeneration, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityImage},
		},
	}
	validator := testValidator(t, record)
	operation := api.OperationImageGeneration
	request := api.MediaRequest{
		Kind: api.RequestKindMedia, SchemaVersion: 1, OperationID: "media", BaseModel: "unknown-model",
		Operation: &operation, Mode: api.ModeUnary, Input: api.MediaInput{Kind: "image_generation", Prompt: "draw"}, Features: []api.Feature{},
	}
	if _, err := validator.AdmitMediaFrame(mediaFrame(request)); Code(err) != api.ErrorInvalidRequest {
		t.Fatalf("unknown media model returned the wrong error: %T %v", err, err)
	}

	catalog, err := model.NewCatalogFromRecords([]ports.ModelRecord{record})
	if err != nil {
		t.Fatal(err)
	}
	unaryProtocol, err := protocol.NewDescriptor("protocol.media", map[api.Operation]protocol.OperationCapability{
		api.OperationImageGeneration: {Modes: []api.DeliveryMode{api.ModeUnary}},
	})
	if err != nil {
		t.Fatal(err)
	}
	streamOnlyService, err := service.NewDescriptor(service.DescriptorConfig{
		Kind: "service.media", Protocols: []string{"protocol.media"}, Models: []string{"media-owner"},
		Operations: map[api.Operation]service.OperationCapability{api.OperationImageGeneration: {Modes: []api.DeliveryMode{api.ModeServerStream}}},
	})
	if err != nil {
		t.Fatal(err)
	}
	validator = New(Config{Catalog: catalog, Protocol: unaryProtocol, Service: streamOnlyService})
	request.BaseModel = "media-owner"
	if _, err := validator.AdmitMediaFrame(mediaFrame(request)); Code(err) != api.ErrorUnsupported {
		t.Fatalf("media capability mismatch returned the wrong error: %T %v", err, err)
	}
}

func TestAdmissionRejectsModelsFromTheWrongRequestProfile(t *testing.T) {
	records := []ports.ModelRecord{
		{BaseModel: "llm-only", Lifecycle: ports.ModelLifecycleActive, Capability: ports.ModelCapability{
			Operation: api.OperationGenerate, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityText},
		}},
		{BaseModel: "media-only", Lifecycle: ports.ModelLifecycleActive, Capability: ports.ModelCapability{
			Operation: api.OperationImageGeneration, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityImage},
		}},
	}
	validator := testValidator(t, records...)
	llm := validAdmissionRequest("media-only", api.ModeUnary)
	llm.Operation = nil
	if _, err := validator.AdmitLLMFrame(llmFrame(llm)); Code(err) != api.ErrorInvalidRequest || !strings.Contains(err.Error(), "outside kernel_llm") {
		t.Fatalf("media model entered LLM profile: %T %v", err, err)
	}
	media := api.MediaRequest{
		Kind: api.RequestKindMedia, SchemaVersion: 1, OperationID: "wrong-profile", BaseModel: "llm-only",
		Mode: api.ModeUnary, Input: api.MediaInput{Kind: "image_generation", Prompt: "draw"},
	}
	if _, err := validator.AdmitMediaFrame(mediaFrame(media)); Code(err) != api.ErrorInvalidRequest {
		t.Fatalf("LLM model entered media profile: %T %v", err, err)
	}
}

func TestAdmissionNormalizesModalityFromTheResolvedOperation(t *testing.T) {
	validator := testValidator(t, ports.ModelRecord{
		BaseModel: "image-normalized", Lifecycle: ports.ModelLifecycleActive,
		Capability: ports.ModelCapability{
			Operation: api.OperationImageGeneration, InputModalities: []api.Modality{api.ModalityText}, OutputModalities: []api.Modality{api.ModalityImage},
		},
	})
	request := api.MediaRequest{
		Kind: api.RequestKindMedia, SchemaVersion: 1, OperationID: "modality", BaseModel: "image-normalized",
		Modality: api.ModalityVideo, Mode: api.ModeUnary,
		Input: api.MediaInput{Kind: "image_generation", Prompt: "draw"},
	}
	admitted, err := validator.AdmitMediaFrame(mediaFrame(request))
	if err != nil {
		t.Fatal(err)
	}
	if admitted.Request().Modality != api.ModalityImage {
		t.Fatalf("caller-provided modality survived normalization: %q", admitted.Request().Modality)
	}
}

func TestEveryMediaOperationAndDeliveryModeUsesOneAdmissionPath(t *testing.T) {
	type mediaCase struct {
		model     string
		operation api.Operation
		inputs    []api.Modality
		output    api.Modality
		input     api.MediaInput
		modality  api.Modality
	}
	cases := []mediaCase{
		{model: "image-all-modes", operation: api.OperationImageGeneration, inputs: []api.Modality{api.ModalityText, api.ModalityImage}, output: api.ModalityImage, modality: api.ModalityImage, input: api.MediaInput{Kind: "image_generation", Prompt: "draw", Source: testArtifact(api.ArtifactKindImage)}},
		{model: "audio-all-modes", operation: api.OperationAudioGeneration, inputs: []api.Modality{api.ModalityText}, output: api.ModalityAudio, modality: api.ModalityAudio, input: api.MediaInput{Kind: "audio_generation", Text: "speak", Voice: "voice"}},
		{model: "music-all-modes", operation: api.OperationMusicGeneration, inputs: []api.Modality{api.ModalityText}, output: api.ModalityMusic, modality: api.ModalityMusic, input: api.MediaInput{Kind: "music_generation", Prompt: "compose"}},
		{model: "video-all-modes", operation: api.OperationVideoGeneration, inputs: []api.Modality{api.ModalityText, api.ModalityImage}, output: api.ModalityVideo, modality: api.ModalityVideo, input: api.MediaInput{Kind: "video_generation", Prompt: "animate", Source: testArtifact(api.ArtifactKindImage)}},
		{model: "transcribe-all-modes", operation: api.OperationAudioTranscription, inputs: []api.Modality{api.ModalityAudio}, output: api.ModalityText, modality: api.ModalityAudio, input: api.MediaInput{Kind: "audio_transcription", Media: testArtifact(api.ArtifactKindAudio)}},
	}
	records := make([]ports.ModelRecord, 0, len(cases))
	for _, testCase := range cases {
		records = append(records, ports.ModelRecord{
			BaseModel: testCase.model, Lifecycle: ports.ModelLifecycleActive,
			Capability: ports.ModelCapability{Operation: testCase.operation, InputModalities: testCase.inputs, OutputModalities: []api.Modality{testCase.output}},
		})
	}
	validator := testValidator(t, records...)
	for _, testCase := range cases {
		for _, mode := range []api.DeliveryMode{api.ModeUnary, api.ModeServerStream, api.ModeAsync} {
			t.Run(string(testCase.operation)+"/"+string(mode), func(t *testing.T) {
				request := api.MediaRequest{
					Kind: api.RequestKindMedia, SchemaVersion: 1, OperationID: "media-matrix", BaseModel: testCase.model,
					Operation: nil, Modality: api.ModalityEmbedding, Mode: mode, Input: testCase.input,
				}
				admitted, err := validator.AdmitMediaFrame(mediaFrame(request))
				if err != nil {
					t.Fatal(err)
				}
				normalized := admitted.Request()
				if normalized.Operation == nil || *normalized.Operation != testCase.operation || normalized.Modality != testCase.modality || normalized.Mode != mode {
					t.Fatalf("normalized media request = %+v", normalized)
				}
			})
		}
	}
}

func admissionCapability(t *testing.T, record ports.ModelRecord) model.Capability {
	t.Helper()
	catalog, err := model.NewCatalogFromRecords([]ports.ModelRecord{record})
	if err != nil {
		t.Fatalf("construct capability fixture: %v", err)
	}
	definition, err := catalog.Lookup(record.BaseModel)
	if err != nil {
		t.Fatal(err)
	}
	capability, ok := definition.Capability(record.Capability.Operation)
	if !ok {
		t.Fatal("fixture capability was not retained")
	}
	return capability
}
