//go:build integration

package integration

// This runner is deliberately small and dependency-free. The JSON Schemas in
// conformance/ are the source of truth; the runner adds the cross-document
// invariants that JSON Schema cannot express (profile/operation alignment,
// request/response correlation, and terminal-only stream semantics).

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

const gatewayInvocationProtocol = "gateway_invocation"

type conformanceManifest struct {
	Schema           string            `json:"$schema"`
	ManifestVersion  int               `json:"manifest_version"`
	ProtocolVersions map[string]int    `json:"protocol_versions"`
	Suites           conformanceSuites `json:"suites"`
}

type conformanceSuites struct {
	StateVectors      []string `json:"state_vectors"`
	WireVectors       []string `json:"wire_vectors"`
	RecoveryScenarios []string `json:"recovery_scenarios"`
	EffectScenarios   []string `json:"effect_scenarios"`
	CanonicalTraces   []string `json:"canonical_traces"`
}

type vectorCase struct {
	Schema      string          `json:"$schema"`
	CaseVersion int             `json:"case_version"`
	CaseID      string          `json:"case_id"`
	Description string          `json:"description"`
	Protocol    vectorProtocol  `json:"protocol"`
	Input       json.RawMessage `json:"input"`
	Expect      json.RawMessage `json:"expect"`
}

type vectorProtocol struct {
	Name    string `json:"name"`
	Version int    `json:"version"`
	Profile string `json:"profile"`
}

type vectorExpectation struct {
	Outcome   string          `json:"outcome"`
	Value     json.RawMessage `json:"value"`
	ErrorCode string          `json:"error_code"`
}

type admissionFailure struct {
	code  string
	cause error
}

func (e *admissionFailure) Error() string { return e.cause.Error() }
func (e *admissionFailure) Unwrap() error { return e.cause }

func TestGatewayConformanceVectors(t *testing.T) {
	root := conformanceRoot(t)
	assertProtocolSchema(t, root)
	manifest := readJSONFile[conformanceManifest](t, filepath.Join(root, "manifest.json"))
	if manifest.ManifestVersion != 1 {
		t.Fatalf("unsupported conformance manifest version: %d", manifest.ManifestVersion)
	}
	if manifest.ProtocolVersions[gatewayInvocationProtocol] != 1 {
		t.Fatalf("manifest must enable %s v1", gatewayInvocationProtocol)
	}
	if len(manifest.Suites.WireVectors) == 0 {
		t.Fatal("gateway conformance suite must contain at least one wire vector")
	}

	paths := append([]string(nil), manifest.Suites.WireVectors...)
	sort.Strings(paths)
	caseIDs := make(map[string]struct{}, len(paths))
	for _, relative := range paths {
		casePath := safeCasePath(t, root, relative)
		vector := readJSONFile[vectorCase](t, casePath)
		if vector.CaseVersion != 1 {
			t.Fatalf("%s: unsupported case version %d", relative, vector.CaseVersion)
		}
		if vector.CaseID == "" || vector.Description == "" {
			t.Fatalf("%s: case_id and description are required", relative)
		}
		if _, exists := caseIDs[vector.CaseID]; exists {
			t.Fatalf("duplicate conformance case id: %s", vector.CaseID)
		}
		caseIDs[vector.CaseID] = struct{}{}
		if vector.Protocol.Name != gatewayInvocationProtocol || vector.Protocol.Version != 1 {
			t.Fatalf("%s: unexpected protocol %+v", relative, vector.Protocol)
		}
		if vector.Protocol.Profile != string(api.ProfileKernelLLM) && vector.Protocol.Profile != string(api.ProfileExecutionMedia) {
			t.Fatalf("%s: unknown profile %q", relative, vector.Protocol.Profile)
		}

		expect := readJSON[vectorExpectation](t, vector.Expect, relative+" expect")
		switch expect.Outcome {
		case "accept":
			request, err := admitProfileRequest(vector.Protocol.Profile, vector.Input)
			if err != nil {
				t.Fatalf("%s: accepted request is not admissible: %v", relative, err)
			}
			response, err := admitProfileResponse(vector.Protocol.Profile, request, expect.Value)
			if err != nil {
				t.Fatalf("%s: invalid terminal response: %v", relative, err)
			}
			if containsForbiddenProjection(vector.Input) || containsForbiddenProjection(expect.Value) {
				t.Fatalf("%s: delta/event projection is forbidden at the runtime boundary", relative)
			}
			_ = response
		case "reject":
			if expect.ErrorCode == "" {
				t.Fatalf("%s: rejection must carry an error code", relative)
			}
			_, err := admitProfileRequest(vector.Protocol.Profile, vector.Input)
			if err == nil {
				t.Fatalf("%s: rejection vector was admitted", relative)
			}
			if actual := rejectionCode(err); actual != expect.ErrorCode {
				t.Fatalf("%s: expected rejection code %q, got %q (%v)", relative, expect.ErrorCode, actual, err)
			}
		default:
			t.Fatalf("%s: unknown expected outcome %q", relative, expect.Outcome)
		}
	}
}

func assertProtocolSchema(t *testing.T, root string) {
	t.Helper()
	document := readJSONFile[map[string]json.RawMessage](t, filepath.Join(root, "schemas", "protocol", "gateway_invocation.v1.schema.json"))
	var dialect, schemaID string
	if err := json.Unmarshal(document["$schema"], &dialect); err != nil || dialect != "https://json-schema.org/draft/2020-12/schema" {
		t.Fatalf("unexpected Gateway protocol schema dialect: %q (%v)", dialect, err)
	}
	if err := json.Unmarshal(document["$id"], &schemaID); err != nil || schemaID != "https://mote.dev/conformance/protocol/gateway_invocation.v1.schema.json" {
		t.Fatalf("unexpected Gateway protocol schema id: %q (%v)", schemaID, err)
	}
	var definitions map[string]json.RawMessage
	if err := json.Unmarshal(document["$defs"], &definitions); err != nil {
		t.Fatalf("decode Gateway protocol definitions: %v", err)
	}
	for _, name := range []string{"request", "llm_request", "media_request", "response", "llm_response", "media_response", "observation_base"} {
		if _, ok := definitions[name]; !ok {
			t.Fatalf("Gateway protocol schema must expose %q", name)
		}
	}
}

func admitProfileRequest(profile string, data []byte) (any, error) {
	switch profile {
	case string(api.ProfileKernelLLM):
		request, err := admitLLMRequest(data)
		return request, err
	case string(api.ProfileExecutionMedia):
		request, err := admitMediaRequest(data)
		return request, err
	default:
		return nil, invalid("unknown profile %q", profile)
	}
}

func admitLLMRequest(data []byte) (api.LLMRequest, error) {
	var request api.LLMRequest
	if err := decodeStrict(data, &request); err != nil {
		return request, invalid("decode LLM request: %v", err)
	}
	if err := requireObjectKeys(data, "kind", "schema_version", "operation_id", "model_id", "operation", "modality", "mode", "input", "features"); err != nil {
		return request, invalid("LLM request: %v", err)
	}
	if request.Kind != api.RequestKindLLM {
		return request, invalid("LLM request kind must be %q", api.RequestKindLLM)
	}
	if request.SchemaVersion != 1 {
		return request, invalid("unsupported schema_version %d", request.SchemaVersion)
	}
	if request.Operation == api.OperationGenerate {
		if request.Modality != api.ModalityText || (request.Mode != api.ModeUnary && request.Mode != api.ModeServerStream) {
			return request, invalid("generate requires text modality and unary/server_stream mode")
		}
		if request.Input.Kind != "generate" || len(request.Input.Messages) == 0 {
			return request, invalid("generate input requires kind=generate and messages")
		}
	} else if request.Operation == api.OperationRealtime {
		if request.Modality != api.ModalityAudio || request.Mode != api.ModeDuplex || request.Input.Kind != "realtime" {
			return request, invalid("realtime requires audio modality, duplex mode, and kind=realtime")
		}
	} else {
		return request, invalid("operation %q is outside kernel_llm profile", request.Operation)
	}
	if err := validateRequestIdentity(request.OperationID, request.ModelID); err != nil {
		return request, invalid("LLM request: %v", err)
	}
	if err := validateFeatures(request.Features, true); err != nil {
		return request, invalid("LLM request: %v", err)
	}
	return request, nil
}

func admitMediaRequest(data []byte) (api.MediaRequest, error) {
	var request api.MediaRequest
	if err := decodeStrict(data, &request); err != nil {
		return request, invalid("decode media request: %v", err)
	}
	if err := requireObjectKeys(data, "kind", "schema_version", "operation_id", "model_id", "operation", "modality", "mode", "input", "features"); err != nil {
		return request, invalid("media request: %v", err)
	}
	if request.Kind != api.RequestKindMedia {
		return request, invalid("media request kind must be %q", api.RequestKindMedia)
	}
	if request.SchemaVersion != 1 {
		return request, invalid("unsupported schema_version %d", request.SchemaVersion)
	}
	if err := validateRequestIdentity(request.OperationID, request.ModelID); err != nil {
		return request, invalid("media request: %v", err)
	}
	if request.Mode != api.ModeUnary && request.Mode != api.ModeServerStream && request.Mode != api.ModeAsync {
		return request, invalid("media profile does not support mode %q", request.Mode)
	}
	wantModality, wantKind := mediaOperationShape(request.Operation)
	if wantModality == "" || request.Modality != wantModality || request.Input.Kind != wantKind {
		return request, invalid("operation/modality/input kind combination is invalid")
	}
	if err := validateMediaInput(request); err != nil {
		return request, invalid("media request: %v", err)
	}
	if err := validateFeatures(request.Features, false); err != nil {
		return request, invalid("media request: %v", err)
	}
	return request, nil
}

func validateMediaInput(request api.MediaRequest) error {
	switch request.Operation {
	case api.OperationImageGeneration, api.OperationMusicGeneration, api.OperationVideoGeneration:
		if strings.TrimSpace(request.Input.Prompt) == "" {
			return errors.New("prompt is required")
		}
	case api.OperationAudioGeneration:
		if strings.TrimSpace(request.Input.Text) == "" || strings.TrimSpace(request.Input.Voice) == "" {
			return errors.New("text and voice are required")
		}
	case api.OperationAudioTranscription:
		if request.Input.Media == nil {
			return errors.New("media artifact is required")
		}
	default:
		return fmt.Errorf("operation %q is outside execution_media profile", request.Operation)
	}
	return nil
}

func mediaOperationShape(operation api.Operation) (api.Modality, string) {
	switch operation {
	case api.OperationImageGeneration:
		return api.ModalityImage, "image_generation"
	case api.OperationAudioGeneration:
		return api.ModalityAudio, "audio_generation"
	case api.OperationMusicGeneration:
		return api.ModalityMusic, "music_generation"
	case api.OperationVideoGeneration:
		return api.ModalityVideo, "video_generation"
	case api.OperationAudioTranscription:
		return api.ModalityAudio, "audio_transcription"
	default:
		return "", ""
	}
}

func admitProfileResponse(profile string, request any, data []byte) (any, error) {
	switch profile {
	case string(api.ProfileKernelLLM):
		llmRequest, ok := request.(api.LLMRequest)
		if !ok {
			return nil, invalid("kernel profile request has wrong nominal type")
		}
		return admitLLMResponse(llmRequest, data)
	case string(api.ProfileExecutionMedia):
		mediaRequest, ok := request.(api.MediaRequest)
		if !ok {
			return nil, invalid("media profile request has wrong nominal type")
		}
		return admitMediaResponse(mediaRequest, data)
	default:
		return nil, invalid("unknown profile %q", profile)
	}
}

func admitLLMResponse(request api.LLMRequest, data []byte) (api.LLMResponse, error) {
	var response api.LLMResponse
	if err := decodeStrict(data, &response); err != nil {
		return response, invalid("decode LLM response: %v", err)
	}
	if err := requireObjectKeys(data, "kind", "schema_version", "operation_id", "mode", "terminal", "observation", "receipt"); err != nil {
		return response, invalid("LLM response: %v", err)
	}
	if response.Kind != api.ResponseKindLLM || response.SchemaVersion != 1 || response.OperationID != request.OperationID || response.Mode != request.Mode {
		return response, invalid("LLM response envelope does not correlate with request")
	}
	if response.Terminal.Outcome == api.OutcomeSubmitted || response.Terminal.Outcome == "" {
		return response, invalid("LLM response has invalid terminal outcome")
	}
	if response.Terminal.Outcome == api.OutcomeSucceeded {
		if response.Terminal.Result == nil || response.Terminal.Result.Kind != api.ResultGenerate {
			return response, invalid("successful LLM response requires a complete generate result")
		}
		if response.Terminal.Result.Text == "" && len(response.Terminal.Result.Structured) == 0 && len(response.Terminal.Result.ToolCalls) == 0 {
			return response, invalid("successful LLM result cannot be empty")
		}
	} else if response.Terminal.Error == nil {
		return response, invalid("non-success LLM response requires a safe error")
	}
	if err := validateLLMObservation(request, response); err != nil {
		return response, err
	}
	return response, nil
}

func admitMediaResponse(request api.MediaRequest, data []byte) (api.MediaResponse, error) {
	var response api.MediaResponse
	if err := decodeStrict(data, &response); err != nil {
		return response, invalid("decode media response: %v", err)
	}
	if err := requireObjectKeys(data, "kind", "schema_version", "operation_id", "mode", "terminal", "observation", "receipt"); err != nil {
		return response, invalid("media response: %v", err)
	}
	if response.Kind != api.ResponseKindMedia || response.SchemaVersion != 1 || response.OperationID != request.OperationID || response.Mode != request.Mode {
		return response, invalid("media response envelope does not correlate with request")
	}
	switch response.Terminal.Outcome {
	case api.OutcomeSucceeded:
		if response.Terminal.Result == nil {
			return response, invalid("successful media response requires a result")
		}
		want, _ := mediaOperationShape(request.Operation)
		if response.Terminal.Result.Kind == api.ResultKind("") || (request.Operation != api.OperationAudioTranscription && response.Terminal.Result.Kind != api.ResultKind(want)) {
			return response, invalid("media result kind does not match operation")
		}
		if request.Operation == api.OperationAudioTranscription && response.Terminal.Result.Kind != api.ResultAudioTranscription {
			return response, invalid("transcription requires a transcript result")
		}
	case api.OutcomeSubmitted:
		if request.Mode != api.ModeAsync || response.Terminal.Task == nil || response.Terminal.Task.OperationID != request.OperationID {
			return response, invalid("submitted media response requires an async task correlated to the operation")
		}
	default:
		if response.Terminal.Error == nil {
			return response, invalid("non-success media response requires a safe error")
		}
	}
	if request.Mode != api.ModeAsync && response.Terminal.Outcome == api.OutcomeSubmitted {
		return response, invalid("only async media calls may be submitted")
	}
	if err := validateMediaObservation(request, response); err != nil {
		return response, err
	}
	return response, nil
}

func validateLLMObservation(request api.LLMRequest, response api.LLMResponse) error {
	o := response.Observation
	if o.SchemaVersion != 1 || o.OperationID != request.OperationID || o.Operation != request.Operation || o.Modality != request.Modality || o.Mode != request.Mode || o.Input.Kind != request.Input.Kind {
		return invalid("LLM observation does not correlate with request")
	}
	if response.Receipt.OperationID != request.OperationID || response.Receipt.ReceiptID == "" || response.Receipt.Revision < 1 {
		return invalid("LLM receipt is incomplete or mismatched")
	}
	return validateTiming(request.Mode, o.Timing)
}

func validateMediaObservation(request api.MediaRequest, response api.MediaResponse) error {
	o := response.Observation
	if o.SchemaVersion != 1 || o.OperationID != request.OperationID || o.Operation != request.Operation || o.Modality != request.Modality || o.Mode != request.Mode || o.Input.Kind != request.Input.Kind {
		return invalid("media observation does not correlate with request")
	}
	if response.Receipt.OperationID != request.OperationID || response.Receipt.ReceiptID == "" || response.Receipt.Revision < 1 {
		return invalid("media receipt is incomplete or mismatched")
	}
	return validateTiming(request.Mode, o.Timing)
}

func validateTiming(mode api.DeliveryMode, timing api.StreamSummary) error {
	if timing.LatencyMS < 0 || timing.ChunkCount < 0 {
		return invalid("timing values cannot be negative")
	}
	streamed := mode == api.ModeServerStream || mode == api.ModeDuplex
	if timing.Streamed != streamed {
		return invalid("timing streamed flag does not match mode")
	}
	if streamed && timing.ChunkCount < 1 {
		return invalid("streamed terminal requires an aggregate chunk count")
	}
	if !streamed && timing.ChunkCount != 0 {
		return invalid("non-stream terminal must have zero chunk count")
	}
	return nil
}

func validateRequestIdentity(operationID, modelID string) error {
	if strings.TrimSpace(operationID) == "" || strings.TrimSpace(modelID) == "" {
		return errors.New("operation_id and model_id are required")
	}
	return nil
}

func validateFeatures(features []api.Feature, llm bool) error {
	seen := make(map[api.Feature]struct{}, len(features))
	for _, feature := range features {
		if _, ok := seen[feature]; ok {
			return fmt.Errorf("duplicate feature %q", feature)
		}
		seen[feature] = struct{}{}
		if llm {
			switch feature {
			case api.FeatureToolCalls, api.FeatureStructured, api.FeaturePromptCache, api.FeatureUsage:
			default:
				return fmt.Errorf("feature %q is not supported by kernel_llm", feature)
			}
		} else if feature != api.FeaturePromptCache && feature != api.FeatureUsage {
			return fmt.Errorf("feature %q is not supported by execution_media", feature)
		}
	}
	return nil
}

func invalid(format string, args ...any) error {
	return &admissionFailure{code: "INVALID_REQUEST", cause: fmt.Errorf(format, args...)}
}

func rejectionCode(err error) string {
	var failure *admissionFailure
	if errors.As(err, &failure) {
		return failure.code
	}
	return "INVALID_REQUEST"
}

func requireObjectKeys(data []byte, keys ...string) error {
	var object map[string]json.RawMessage
	if err := json.Unmarshal(data, &object); err != nil {
		return err
	}
	for _, key := range keys {
		if _, ok := object[key]; !ok {
			return fmt.Errorf("%s is required", key)
		}
	}
	return nil
}

func containsForbiddenProjection(data []byte) bool {
	var value any
	if json.Unmarshal(data, &value) != nil {
		return false
	}
	var visit func(any, bool) bool
	visit = func(current any, modelOwned bool) bool {
		if modelOwned {
			return false
		}
		switch node := current.(type) {
		case map[string]any:
			for key, child := range node {
				lower := strings.ToLower(key)
				// JSON owned by the model (a structured result, tool schema, or
				// finalized tool arguments) may legitimately contain a property
				// named event/delta. It is not a transport projection.
				childModelOwned := lower == "structured" || lower == "input_schema" || lower == "arguments"
				if childModelOwned {
					continue
				}
				// Aggregate *_event_count fields are terminal timing facts and
				// are explicitly allowed. Payload-bearing deltas/events are not.
				if strings.Contains(lower, "delta") ||
					(strings.Contains(lower, "event") && !strings.Contains(lower, "event_count")) {
					return true
				}
				if visit(child, false) {
					return true
				}
			}
		case []any:
			for _, child := range node {
				if visit(child, false) {
					return true
				}
			}
		}
		return false
	}
	return visit(value, false)
}

func conformanceRoot(t *testing.T) string {
	t.Helper()
	_, source, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(source), "..", "..", "..", "..", "conformance"))
}

func safeCasePath(t *testing.T, root, relative string) string {
	t.Helper()
	if relative == "" || filepath.IsAbs(relative) {
		t.Fatalf("manifest case path must be relative: %q", relative)
	}
	clean := filepath.Clean(filepath.FromSlash(relative))
	if clean == "." || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		t.Fatalf("manifest case path escapes conformance root: %q", relative)
	}
	if !strings.HasPrefix(filepath.ToSlash(clean), "vectors/wire/") || !strings.HasSuffix(clean, ".json") {
		t.Fatalf("unexpected Gateway case path: %q", relative)
	}
	path := filepath.Join(root, clean)
	resolvedRoot, err := filepath.Abs(root)
	if err != nil {
		t.Fatalf("resolve conformance root: %v", err)
	}
	resolvedPath, err := filepath.Abs(path)
	if err != nil {
		t.Fatalf("resolve case path: %v", err)
	}
	if resolvedPath != resolvedRoot && !strings.HasPrefix(resolvedPath, resolvedRoot+string(filepath.Separator)) {
		t.Fatalf("manifest case path escapes conformance root: %q", relative)
	}
	return path
}

func readJSONFile[T any](t *testing.T, path string) T {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return readJSON[T](t, data, path)
}

func readJSON[T any](t *testing.T, data []byte, name string) T {
	t.Helper()
	var value T
	if err := decodeStrict(data, &value); err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
	return value
}

func decodeStrict[T any](data []byte, value *T) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return errors.New("multiple JSON values")
		}
		return err
	}
	return nil
}
