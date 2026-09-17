package admission

import (
	"errors"
	"fmt"
	"strings"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/protocol"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/service"
)

const currentSchemaVersion = 1

// Config supplies the exact model catalog and already-bound protocol/service
type Config struct {
	Catalog  model.CatalogReader
	Protocol protocol.Facts
	Service  service.Facts
}

// Validator is immutable after construction. It performs the only admission
// step used by application and conformance code.
type Validator struct {
	catalog  model.CatalogReader
	protocol protocol.Facts
	service  service.Facts
}

// New constructs a validator from composition already checked by Gateway's
// public boundary.
func New(config Config) Validator {
	return Validator{catalog: config.Catalog, protocol: config.Protocol, service: config.Service}
}

// AdmitLLMFrame validates and normalizes one typed invocation frame, then
// freezes it into an immutable admitted request. Raw wire decoding and schema
// validation are owned by mote-infra/invocation; this method consumes only the
// resulting typed frame.
func (validator Validator) AdmitLLMFrame(frame api.LLMRequestFrame) (AdmittedLLM, error) {
	request := frame.Request
	if err := validateEnvelope(request.Kind, api.RequestKindLLM, request.SchemaVersion, request.OperationID, request.BaseModel); err != nil {
		return AdmittedLLM{}, invalid(err)
	}
	if request.Input.Reasoning != nil {
		if err := request.Input.Reasoning.Validate(); err != nil {
			return AdmittedLLM{}, invalid(err)
		}
	}
	operation, definition, capability, err := validator.resolveModel(request.BaseModel, request.Operation)
	if err != nil {
		return AdmittedLLM{}, err
	}
	request.Operation = &operation
	request.Modality, _ = operation.PrimaryModality()
	required, err := reduceLLMRequirements(request)
	if err != nil {
		return AdmittedLLM{}, invalid(err)
	}
	request.Features = required.features
	if err := validateLLMShape(request, required, capability); err != nil {
		return AdmittedLLM{}, ensureAdmissionError(err)
	}
	request.Input.Parameters = capability.ResolveGenerationParameters(request.Input.Parameters)
	resolvedReasoning, err := capability.ResolveReasoning(request.Input.Reasoning)
	if err != nil {
		return AdmittedLLM{}, classifyModelError(err)
	}
	if err := validator.validateCapabilities(request.BaseModel, operation, request.Mode, required.features, resolvedReasoning, capability); err != nil {
		return AdmittedLLM{}, err
	}
	request.Input.Reasoning = resolvedReasoning
	return newAdmittedLLM(request, definition, validator.protocol.Name(), validator.service.Name()), nil
}

// AdmitMediaFrame validates and normalizes one typed media invocation frame
// and freezes it into an immutable admitted request.
func (validator Validator) AdmitMediaFrame(frame api.MediaRequestFrame) (AdmittedMedia, error) {
	request := frame.Request
	if err := validateEnvelope(request.Kind, api.RequestKindMedia, request.SchemaVersion, request.OperationID, request.BaseModel); err != nil {
		return AdmittedMedia{}, invalid(err)
	}
	operation, definition, capability, err := validator.resolveModel(request.BaseModel, request.Operation)
	if err != nil {
		return AdmittedMedia{}, err
	}
	request.Operation = &operation
	request.Modality, _ = operation.PrimaryModality()
	required, err := reduceMediaRequirements(request)
	if err != nil {
		return AdmittedMedia{}, invalid(err)
	}
	request.Features = required.features
	if err := validateMediaShape(request, required, capability); err != nil {
		return AdmittedMedia{}, ensureAdmissionError(err)
	}
	request.Input.Parameters = capability.ResolveGenerationParameters(request.Input.Parameters)
	if err := validator.validateCapabilities(request.BaseModel, operation, request.Mode, required.features, nil, capability); err != nil {
		return AdmittedMedia{}, err
	}
	return newAdmittedMedia(request, definition, validator.protocol.Name(), validator.service.Name()), nil
}

func (validator Validator) resolveModel(baseModel string, requested *api.Operation) (api.Operation, model.Definition, model.Capability, error) {
	definition, err := validator.catalog.Current().Lookup(baseModel)
	if err != nil {
		return "", model.Definition{}, model.Capability{}, classifyModelError(err)
	}
	operation, err := definition.ResolveOperation(requested)
	if err != nil {
		return "", model.Definition{}, model.Capability{}, classifyModelError(err)
	}
	capability, ok := definition.Capability(operation)
	if !ok {
		return "", model.Definition{}, model.Capability{}, invalid(&model.OperationMismatchError{
			BaseModel: baseModel,
			Requested: operation,
			Available: definition.Operation(),
		})
	}
	return operation, definition, capability, nil
}

func (validator Validator) validateCapabilities(baseModel string, operation api.Operation, mode api.DeliveryMode, features []api.Feature, reasoning *api.ReasoningConfig, capability model.Capability) error {
	if !validator.service.SupportsProtocol(validator.protocol.Name()) {
		return unsupported(fmt.Errorf("service %q does not support protocol %q", validator.service.Name(), validator.protocol.Name()))
	}
	if !validator.service.SupportsModel(baseModel) {
		return unsupported(fmt.Errorf("service %q cannot deploy model %q", validator.service.Name(), baseModel))
	}
	if !validator.protocol.SupportsOperation(operation, mode) {
		return unsupported(fmt.Errorf("protocol %q does not support operation %q in mode %q", validator.protocol.Name(), operation, mode))
	}
	if !validator.service.SupportsOperation(operation, mode) {
		return unsupported(fmt.Errorf("service %q does not support operation %q in mode %q", validator.service.Name(), operation, mode))
	}
	if err := validator.validateFeatureIntersection(operation, features, capability); err != nil {
		return err
	}
	if !validator.protocol.SupportsReasoning(operation, reasoning) {
		return unsupported(fmt.Errorf("protocol %q cannot express the selected reasoning preference", validator.protocol.Name()))
	}
	if !validator.service.SupportsReasoning(operation, reasoning) {
		return unsupported(fmt.Errorf("service %q cannot express the selected reasoning preference", validator.service.Name()))
	}
	return nil
}

func (validator Validator) validateFeatureIntersection(operation api.Operation, features []api.Feature, capability model.Capability) error {
	for _, feature := range features {
		if err := validateModelFeature(feature, capability); err != nil {
			return err
		}
		if !validator.protocol.SupportsFeature(operation, feature) {
			return unsupported(fmt.Errorf("protocol %q does not support feature %q", validator.protocol.Name(), feature))
		}
		if !validator.service.SupportsFeature(operation, feature) {
			return unsupported(fmt.Errorf("service %q does not support feature %q", validator.service.Name(), feature))
		}
	}
	return nil
}

func validateModelFeature(feature api.Feature, capability model.Capability) error {
	if feature != api.FeatureToolCalls && feature != api.FeatureStructured {
		return nil
	}
	if !capability.SupportsFeature(feature) {
		return unsupported(fmt.Errorf("model %q does not support feature %q", capability.BaseModel(), feature))
	}
	return nil
}

func validateEnvelope(kind, expectedKind api.RequestKind, schemaVersion int, operationID, baseModel string) error {
	if kind != expectedKind {
		return fmt.Errorf("request kind must be %q", expectedKind)
	}
	if schemaVersion != currentSchemaVersion {
		return fmt.Errorf("unsupported schema_version %d", schemaVersion)
	}
	if strings.TrimSpace(operationID) == "" {
		return fmt.Errorf("operation_id is required")
	}
	if err := api.ValidateBaseModel(baseModel); err != nil {
		return fmt.Errorf("base_model: %w", err)
	}
	return nil
}

func validateLLMShape(request api.LLMRequest, required requirements, capability model.Capability) error {
	if request.Operation == nil || !(*request.Operation).IsLLM() {
		return fmt.Errorf("operation %q is outside kernel_llm profile", operationValue(request.Operation))
	}
	if err := validateLLMOperationShape(request); err != nil {
		return err
	}
	for _, modality := range required.inputModalities {
		if !capability.SupportsInputModality(modality) {
			return unsupported(fmt.Errorf("model %q does not accept input modality %q", capability.BaseModel(), modality))
		}
	}
	for _, modality := range required.outputModalities {
		if !capability.SupportsOutputModality(modality) {
			return unsupported(fmt.Errorf("model %q does not produce modality %q", capability.BaseModel(), modality))
		}
	}
	return nil
}

func validateLLMOperationShape(request api.LLMRequest) error {
	switch *request.Operation {
	case api.OperationGenerate:
		return validateGenerateShape(request)
	case api.OperationRealtime:
		return validateRealtimeShape(request)
	}
	return nil
}

func validateGenerateShape(request api.LLMRequest) error {
	if !isLLMStreamMode(request.Mode) {
		return fmt.Errorf("generate requires unary/server_stream mode")
	}
	if request.Input.Kind != "generate" || len(request.Input.Messages) == 0 {
		return fmt.Errorf("generate input requires kind=generate and messages")
	}
	return nil
}

func validateRealtimeShape(request api.LLMRequest) error {
	if request.Mode != api.ModeDuplex || request.Input.Kind != "realtime" {
		return fmt.Errorf("realtime requires duplex mode and kind=realtime")
	}
	return nil
}

func isLLMStreamMode(mode api.DeliveryMode) bool {
	return mode == api.ModeUnary || mode == api.ModeServerStream
}

func validateMediaShape(request api.MediaRequest, required requirements, capability model.Capability) error {
	if request.Operation == nil || !(*request.Operation).IsMedia() {
		return fmt.Errorf("operation %q is outside execution_media profile", operationValue(request.Operation))
	}
	if !isMediaMode(request.Mode) {
		return fmt.Errorf("media profile does not support mode %q", request.Mode)
	}
	wantKind := mediaOperationKind(*request.Operation)
	if request.Input.Kind != wantKind {
		return fmt.Errorf("operation/input kind combination is invalid")
	}
	if err := validateMediaInput(request); err != nil {
		return err
	}
	for _, modality := range required.inputModalities {
		if !capability.SupportsInputModality(modality) {
			return unsupported(fmt.Errorf("model %q does not accept input modality %q", capability.BaseModel(), modality))
		}
	}
	for _, modality := range required.outputModalities {
		if !capability.SupportsOutputModality(modality) {
			return unsupported(fmt.Errorf("model %q does not produce modality %q", capability.BaseModel(), modality))
		}
	}
	return nil
}

func isMediaMode(mode api.DeliveryMode) bool {
	return mode == api.ModeUnary || mode == api.ModeServerStream || mode == api.ModeAsync
}

func validateMediaInput(request api.MediaRequest) error {
	rule, ok := mediaShapes[*request.Operation]
	if !ok {
		return fmt.Errorf("operation %q has no media input rule", *request.Operation)
	}
	if err := validateMediaText(request, rule); err != nil {
		return err
	}
	return validateMediaArtifacts(request, rule)
}

func validateMediaText(request api.MediaRequest, rule mediaShape) error {
	if rule.promptInput && strings.TrimSpace(request.Input.Prompt) == "" {
		return fmt.Errorf("prompt is required")
	}
	if rule.textInput && (strings.TrimSpace(request.Input.Text) == "" || strings.TrimSpace(request.Input.Voice) == "") {
		return fmt.Errorf("text and voice are required")
	}
	return nil
}

func validateMediaArtifacts(request api.MediaRequest, rule mediaShape) error {
	if !rule.allowSource && request.Input.Source != nil {
		return fmt.Errorf("operation %q does not accept a source artifact", *request.Operation)
	}
	if !rule.allowMedia && request.Input.Media != nil {
		return fmt.Errorf("operation %q does not accept a media artifact", *request.Operation)
	}
	if rule.requireMedia && request.Input.Media == nil {
		return fmt.Errorf("media artifact is required")
	}
	return nil
}

func validateProfileFeature(feature api.Feature, llm bool) error {
	if !feature.IsValid() {
		return fmt.Errorf("unsupported feature %q", feature)
	}
	if llm {
		switch feature {
		case api.FeatureToolCalls, api.FeatureStructured, api.FeaturePromptCache, api.FeatureUsage:
			return nil
		default:
			return fmt.Errorf("feature %q is not supported by kernel_llm", feature)
		}
	}
	if feature != api.FeaturePromptCache && feature != api.FeatureUsage {
		return fmt.Errorf("feature %q is not supported by execution_media", feature)
	}
	return nil
}

func operationValue(operation *api.Operation) api.Operation {
	if operation == nil {
		return ""
	}
	return *operation
}

func mediaOperationKind(operation api.Operation) string {
	shape, ok := mediaShapes[operation]
	if !ok {
		return ""
	}
	return shape.kind
}

type mediaShape struct {
	kind           string
	promptInput    bool
	textInput      bool
	requireMedia   bool
	allowSource    bool
	allowMedia     bool
	outputModality api.Modality
}

var mediaShapes = map[api.Operation]mediaShape{
	api.OperationImageGeneration:    {kind: "image_generation", promptInput: true, allowSource: true, outputModality: api.ModalityImage},
	api.OperationAudioGeneration:    {kind: "audio_generation", textInput: true, outputModality: api.ModalityAudio},
	api.OperationMusicGeneration:    {kind: "music_generation", promptInput: true, outputModality: api.ModalityMusic},
	api.OperationVideoGeneration:    {kind: "video_generation", promptInput: true, allowSource: true, outputModality: api.ModalityVideo},
	api.OperationAudioTranscription: {kind: "audio_transcription", requireMedia: true, allowMedia: true, outputModality: api.ModalityText},
}

func classifyModelError(err error) error {
	var reasoningUnsupported *model.ReasoningUnsupportedError
	if errors.As(err, &reasoningUnsupported) {
		return unsupported(err)
	}
	return invalid(err)
}

// ensureAdmissionError keeps every deterministic request failure on the same
// typed boundary. Shape validators intentionally return ordinary errors so
// they remain reusable inside the model/profile checks; the application and
// public gateway seams must never have to guess whether such an error came
// from admission or from a downstream adapter.
func ensureAdmissionError(err error) error {
	if err == nil {
		return nil
	}
	if _, ok := AsAdmission(err); ok {
		return err
	}
	return invalid(err)
}

// AdmissionError carries the stable public error code while preserving the
// owner error for diagnostics and tests.
type AdmissionError struct {
	Code api.ErrorCode
	Err  error
}

func (err *AdmissionError) Error() string { return err.Err.Error() }
func (err *AdmissionError) Unwrap() error { return err.Err }

func invalid(err error) error     { return &AdmissionError{Code: api.ErrorInvalidRequest, Err: err} }
func unsupported(err error) error { return &AdmissionError{Code: api.ErrorUnsupported, Err: err} }

// Code returns the stable public admission code for an error.
func Code(err error) api.ErrorCode {
	if admissionError, ok := AsAdmission(err); ok {
		return admissionError.Code
	}
	return ""
}

// AsAdmission reports whether err was produced by this admission owner. It is
// consumed immediately at the validator boundary, before an adapter runs; it
// must not be used to classify a downstream error chain.
func AsAdmission(err error) (*AdmissionError, bool) {
	var admissionError *AdmissionError
	if !errors.As(err, &admissionError) {
		return nil, false
	}
	return admissionError, true
}
