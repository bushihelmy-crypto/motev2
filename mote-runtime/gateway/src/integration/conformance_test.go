//go:build integration

package integration

// This runner keeps policy in the authoritative JSON Schemas in conformance/;
// it adds only the cross-document invariants that JSON Schema cannot express
// (profile/operation alignment, request/response correlation, and terminal-only
// stream semantics).

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"testing"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/admission"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/testkit"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/ports"
	"github.com/santhosh-tekuri/jsonschema/v6"
)

const (
	gatewayInvocationProtocol = "gateway_invocation"
	gatewayInvocationVersion  = 1
	gatewayInvocationSchema   = "gateway_invocation.v1.schema.json"
)

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

type protocolSchemas struct {
	manifest *jsonschema.Schema
	vector   *jsonschema.Schema
	request  *jsonschema.Schema
	response *jsonschema.Schema
}

func (e *admissionFailure) Error() string { return e.cause.Error() }
func (e *admissionFailure) Unwrap() error { return e.cause }

func TestGatewayConformanceVectors(t *testing.T) {
	root := conformanceRoot(t)
	schemas := assertProtocolSchema(t, root)
	validator := fixtureValidator(t)
	manifestData, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		t.Fatalf("read conformance manifest: %v", err)
	}
	if err := validateSchema(schemas.manifest, manifestData); err != nil {
		t.Fatalf("manifest does not satisfy its authoritative schema: %v", err)
	}
	manifest := readJSON[conformanceManifest](t, manifestData, "manifest.json")
	if manifest.ManifestVersion != 1 {
		t.Fatalf("unsupported conformance manifest version: %d", manifest.ManifestVersion)
	}
	if manifest.ProtocolVersions[gatewayInvocationProtocol] != gatewayInvocationVersion {
		t.Fatalf("manifest must enable %s v%d", gatewayInvocationProtocol, gatewayInvocationVersion)
	}
	if len(manifest.Suites.WireVectors) == 0 {
		t.Fatal("gateway conformance suite must contain at least one wire vector")
	}

	paths := append([]string(nil), manifest.Suites.WireVectors...)
	sort.Strings(paths)
	caseIDs := make(map[string]struct{}, len(paths))
	for _, relative := range paths {
		casePath := safeCasePath(t, root, relative)
		vectorData, err := os.ReadFile(casePath)
		if err != nil {
			t.Fatalf("read %s: %v", relative, err)
		}
		if err := validateSchema(schemas.vector, vectorData); err != nil {
			t.Fatalf("%s does not satisfy its authoritative case schema: %v", relative, err)
		}
		vector := readJSON[vectorCase](t, vectorData, relative)
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
		if vector.Protocol.Name != gatewayInvocationProtocol || vector.Protocol.Version != gatewayInvocationVersion {
			t.Fatalf("%s: unexpected protocol %+v", relative, vector.Protocol)
		}
		if vector.Protocol.Profile != string(api.ProfileKernelLLM) && vector.Protocol.Profile != string(api.ProfileExecutionMedia) {
			t.Fatalf("%s: unknown profile %q", relative, vector.Protocol.Profile)
		}

		expect := readJSON[vectorExpectation](t, vector.Expect, relative+" expect")
		switch expect.Outcome {
		case "accept":
			request, err := admitProfileRequest(schemas.request, validator, vector.Protocol.Profile, vector.Input)
			if err != nil {
				t.Fatalf("%s: accepted request is not admissible: %v", relative, err)
			}
			if err := validateSchema(schemas.response, expect.Value); err != nil {
				t.Fatalf("%s: accepted response does not satisfy the authoritative schema: %v", relative, err)
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
			_, err := admitProfileRequest(schemas.request, validator, vector.Protocol.Profile, vector.Input)
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

func TestAuthoritativeSchemaRejectsAliasesAndNestedShapeViolations(t *testing.T) {
	root := conformanceRoot(t)
	schemas := assertProtocolSchema(t, root)
	valid := readJSONFile[vectorCase](t, filepath.Join(root, "vectors", "wire", "gateway_invocation_unary_text.v1.json"))
	var object map[string]any
	if err := json.Unmarshal(valid.Input, &object); err != nil {
		t.Fatalf("decode fixture input: %v", err)
	}
	cases := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{name: "case-variant-operation", mutate: func(value map[string]any) {
			value["Operation"] = value["operation"]
			delete(value, "operation")
		}},
		{name: "null-features", mutate: func(value map[string]any) {
			value["features"] = nil
		}},
		{name: "null-reasoning", mutate: func(value map[string]any) {
			input := value["input"].(map[string]any)
			input["reasoning"] = nil
		}},
		{name: "case-variant-reasoning", mutate: func(value map[string]any) {
			input := value["input"].(map[string]any)
			input["REASONING"] = map[string]any{"thinking": "adaptive"}
		}},
		{name: "empty-operation", mutate: func(value map[string]any) {
			value["operation"] = ""
		}},
		{name: "empty-message", mutate: func(value map[string]any) {
			input := value["input"].(map[string]any)
			input["messages"] = []any{map[string]any{}}
		}},
		{name: "invalid-operation-id", mutate: func(value map[string]any) {
			value["operation_id"] = "!"
		}},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			candidate := cloneObject(object)
			testCase.mutate(candidate)
			data, err := json.Marshal(candidate)
			if err != nil {
				t.Fatalf("marshal candidate: %v", err)
			}
			if err := validateSchema(schemas.request, data); err == nil {
				t.Fatalf("authoritative schema accepted %s", testCase.name)
			}
		})
	}
}

func TestAuthoritativeResponseSchemaRejectsNonCanonicalRuntimeIdentities(t *testing.T) {
	root := conformanceRoot(t)
	schemas := assertProtocolSchema(t, root)
	vector := readJSONFile[vectorCase](t, filepath.Join(root, "vectors", "wire", "gateway_invocation_unary_text.v1.json"))
	expect := readJSON[vectorExpectation](t, vector.Expect, "unary text expectation")
	var response map[string]any
	if err := json.Unmarshal(expect.Value, &response); err != nil {
		t.Fatalf("decode fixture response: %v", err)
	}
	tests := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{name: "protocol", mutate: func(value map[string]any) {
			observation := value["observation"].(map[string]any)
			model := observation["model"].(map[string]any)
			model["protocol_id"] = "Protocol/Bad"
		}},
		{name: "service", mutate: func(value map[string]any) {
			observation := value["observation"].(map[string]any)
			model := observation["model"].(map[string]any)
			model["service_kind"] = "Service/Bad"
		}},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			candidate := cloneObject(response)
			testCase.mutate(candidate)
			data, err := json.Marshal(candidate)
			if err != nil {
				t.Fatalf("marshal response: %v", err)
			}
			if err := validateSchema(schemas.response, data); err == nil {
				t.Fatalf("response schema accepted non-canonical %s identity", testCase.name)
			}
		})
	}
}

func TestLLMTerminalRejectsCrossDocumentMismatches(t *testing.T) {
	root := conformanceRoot(t)
	schemas := assertProtocolSchema(t, root)
	validator := fixtureValidator(t)
	requestValue, responseData := admittedFixture(
		t,
		root,
		schemas.request,
		validator,
		"gateway_invocation_unary_text.v1.json",
	)
	request, ok := requestValue.(api.LLMRequest)
	if !ok {
		t.Fatalf("fixture admitted as %T, want api.LLMRequest", requestValue)
	}

	tests := []struct {
		name   string
		mutate func(*api.LLMResponse)
	}{
		{name: "operation id", mutate: func(response *api.LLMResponse) { response.OperationID = "another-operation" }},
		{name: "mode", mutate: func(response *api.LLMResponse) { response.Mode = api.ModeServerStream }},
		{name: "submitted outcome", mutate: func(response *api.LLMResponse) { response.Terminal.Outcome = api.OutcomeSubmitted }},
		{name: "missing successful result", mutate: func(response *api.LLMResponse) { response.Terminal.Result = nil }},
		{name: "wrong result kind", mutate: func(response *api.LLMResponse) { response.Terminal.Result.Kind = api.ResultImage }},
		{name: "empty successful result", mutate: func(response *api.LLMResponse) {
			response.Terminal.Result = &api.LLMOutput{Kind: api.ResultGenerate}
		}},
		{name: "failed without error", mutate: func(response *api.LLMResponse) {
			response.Terminal.Outcome = api.OutcomeFailed
			response.Terminal.Result = nil
			response.Terminal.Error = nil
		}},
		{name: "observation operation id", mutate: func(response *api.LLMResponse) { response.Observation.OperationID = "another-operation" }},
		{name: "observation operation", mutate: func(response *api.LLMResponse) { response.Observation.Operation = api.OperationRealtime }},
		{name: "observation modality", mutate: func(response *api.LLMResponse) { response.Observation.Modality = api.ModalityAudio }},
		{name: "observation mode", mutate: func(response *api.LLMResponse) { response.Observation.Mode = api.ModeServerStream }},
		{name: "requested model", mutate: func(response *api.LLMResponse) { response.Observation.Model.RequestedBaseModel = "another-model" }},
		{name: "resolved model", mutate: func(response *api.LLMResponse) { response.Observation.Model.ResolvedBaseModel = "another-model" }},
		{name: "observed input", mutate: func(response *api.LLMResponse) { response.Observation.Input.SystemPrompt = "changed" }},
		{name: "receipt operation id", mutate: func(response *api.LLMResponse) { response.Receipt.OperationID = "another-operation" }},
		{name: "empty receipt id", mutate: func(response *api.LLMResponse) { response.Receipt.ReceiptID = "" }},
		{name: "zero receipt revision", mutate: func(response *api.LLMResponse) { response.Receipt.Revision = 0 }},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			var response api.LLMResponse
			if err := decodeStrict(responseData, &response); err != nil {
				t.Fatalf("decode fixture response: %v", err)
			}
			testCase.mutate(&response)
			if err := admitMutatedResponse(request, response); err == nil {
				t.Fatal("mismatched LLM terminal was accepted")
			}
		})
	}
}

func TestMediaTerminalRejectsCrossDocumentMismatches(t *testing.T) {
	root := conformanceRoot(t)
	schemas := assertProtocolSchema(t, root)
	validator := fixtureValidator(t)
	imageValue, imageResponse := admittedFixture(
		t,
		root,
		schemas.request,
		validator,
		"gateway_invocation_unary_image.v1.json",
	)
	imageRequest, ok := imageValue.(api.MediaRequest)
	if !ok {
		t.Fatalf("fixture admitted as %T, want api.MediaRequest", imageValue)
	}

	tests := []struct {
		name   string
		mutate func(*api.MediaResponse)
	}{
		{name: "operation id", mutate: func(response *api.MediaResponse) { response.OperationID = "another-operation" }},
		{name: "mode", mutate: func(response *api.MediaResponse) { response.Mode = api.ModeServerStream }},
		{name: "missing successful result", mutate: func(response *api.MediaResponse) { response.Terminal.Result = nil }},
		{name: "empty result kind", mutate: func(response *api.MediaResponse) { response.Terminal.Result.Kind = "" }},
		{name: "wrong result kind", mutate: func(response *api.MediaResponse) { response.Terminal.Result.Kind = api.ResultAudio }},
		{name: "failed without error", mutate: func(response *api.MediaResponse) {
			response.Terminal.Outcome = api.OutcomeFailed
			response.Terminal.Result = nil
			response.Terminal.Error = nil
		}},
		{name: "observation operation id", mutate: func(response *api.MediaResponse) { response.Observation.OperationID = "another-operation" }},
		{name: "observation operation", mutate: func(response *api.MediaResponse) { response.Observation.Operation = api.OperationVideoGeneration }},
		{name: "observation modality", mutate: func(response *api.MediaResponse) { response.Observation.Modality = api.ModalityVideo }},
		{name: "observation mode", mutate: func(response *api.MediaResponse) { response.Observation.Mode = api.ModeServerStream }},
		{name: "requested model", mutate: func(response *api.MediaResponse) { response.Observation.Model.RequestedBaseModel = "another-model" }},
		{name: "resolved model", mutate: func(response *api.MediaResponse) { response.Observation.Model.ResolvedBaseModel = "another-model" }},
		{name: "observed input", mutate: func(response *api.MediaResponse) { response.Observation.Input.Prompt = "changed" }},
		{name: "receipt operation id", mutate: func(response *api.MediaResponse) { response.Receipt.OperationID = "another-operation" }},
		{name: "empty receipt id", mutate: func(response *api.MediaResponse) { response.Receipt.ReceiptID = "" }},
		{name: "zero receipt revision", mutate: func(response *api.MediaResponse) { response.Receipt.Revision = 0 }},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			var response api.MediaResponse
			if err := decodeStrict(imageResponse, &response); err != nil {
				t.Fatalf("decode fixture response: %v", err)
			}
			testCase.mutate(&response)
			if err := admitMutatedResponse(imageRequest, response); err == nil {
				t.Fatal("mismatched media terminal was accepted")
			}
		})
	}

	transcriptionValue, transcriptionResponse := admittedFixture(
		t,
		root,
		schemas.request,
		validator,
		"gateway_invocation_unary_transcription.v1.json",
	)
	transcriptionRequest := transcriptionValue.(api.MediaRequest)
	var wrongTranscription api.MediaResponse
	if err := decodeStrict(transcriptionResponse, &wrongTranscription); err != nil {
		t.Fatalf("decode transcription response: %v", err)
	}
	wrongTranscription.Terminal.Result.Kind = api.ResultAudio
	if err := admitMutatedResponse(transcriptionRequest, wrongTranscription); err == nil {
		t.Fatal("transcription accepted a non-transcript result")
	}
}

func TestSubmittedMediaTerminalRequiresAsyncCorrelatedTask(t *testing.T) {
	root := conformanceRoot(t)
	schemas := assertProtocolSchema(t, root)
	requestValue, responseData := admittedFixture(
		t,
		root,
		schemas.request,
		fixtureValidator(t),
		"gateway_invocation_async_video.v1.json",
	)
	request := requestValue.(api.MediaRequest)

	tests := []struct {
		name          string
		mutateRequest func(*api.MediaRequest)
		mutate        func(*api.MediaResponse)
	}{
		{name: "missing task", mutate: func(response *api.MediaResponse) { response.Terminal.Task = nil }},
		{name: "task operation mismatch", mutate: func(response *api.MediaResponse) { response.Terminal.Task.OperationID = "another-operation" }},
		{name: "submitted from unary", mutateRequest: func(request *api.MediaRequest) { request.Mode = api.ModeUnary }, mutate: func(response *api.MediaResponse) {
			response.Mode = api.ModeUnary
			response.Observation.Mode = api.ModeUnary
		}},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			candidateRequest := request
			var response api.MediaResponse
			if err := decodeStrict(responseData, &response); err != nil {
				t.Fatalf("decode fixture response: %v", err)
			}
			if testCase.mutateRequest != nil {
				testCase.mutateRequest(&candidateRequest)
			}
			testCase.mutate(&response)
			if err := admitMutatedResponse(candidateRequest, response); err == nil {
				t.Fatal("invalid submitted terminal was accepted")
			}
		})
	}
}

func TestTerminalTimingMatchesDeliveryMode(t *testing.T) {
	tests := []struct {
		name   string
		mode   api.DeliveryMode
		timing api.StreamSummary
		valid  bool
	}{
		{name: "unary", mode: api.ModeUnary, timing: api.StreamSummary{}, valid: true},
		{name: "async", mode: api.ModeAsync, timing: api.StreamSummary{}, valid: true},
		{name: "server stream", mode: api.ModeServerStream, timing: api.StreamSummary{Streamed: true, ChunkCount: 1}, valid: true},
		{name: "duplex", mode: api.ModeDuplex, timing: api.StreamSummary{Streamed: true, ChunkCount: 1}, valid: true},
		{name: "negative latency", mode: api.ModeUnary, timing: api.StreamSummary{LatencyMS: -1}},
		{name: "negative chunks", mode: api.ModeUnary, timing: api.StreamSummary{ChunkCount: -1}},
		{name: "unary marked streamed", mode: api.ModeUnary, timing: api.StreamSummary{Streamed: true}},
		{name: "unary has chunks", mode: api.ModeUnary, timing: api.StreamSummary{ChunkCount: 1}},
		{name: "stream not marked streamed", mode: api.ModeServerStream, timing: api.StreamSummary{ChunkCount: 1}},
		{name: "stream has no chunks", mode: api.ModeServerStream, timing: api.StreamSummary{Streamed: true}},
	}
	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			err := validateTiming(testCase.mode, testCase.timing)
			if testCase.valid && err != nil {
				t.Fatalf("valid timing rejected: %v", err)
			}
			if !testCase.valid && err == nil {
				t.Fatal("invalid timing accepted")
			}
		})
	}
}

func admittedFixture(
	t *testing.T,
	root string,
	schema *jsonschema.Schema,
	validator admission.Validator,
	filename string,
) (any, json.RawMessage) {
	t.Helper()
	vector := readJSONFile[vectorCase](t, filepath.Join(root, "vectors", "wire", filename))
	request, err := admitProfileRequest(schema, validator, vector.Protocol.Profile, vector.Input)
	if err != nil {
		t.Fatalf("admit fixture request: %v", err)
	}
	expect := readJSON[vectorExpectation](t, vector.Expect, filename+" expectation")
	if expect.Outcome != "accept" {
		t.Fatalf("fixture %s is not an acceptance vector", filename)
	}
	return request, expect.Value
}

func admitMutatedResponse(request, response any) error {
	data, err := json.Marshal(response)
	if err != nil {
		return err
	}
	switch typed := request.(type) {
	case api.LLMRequest:
		_, err = admitLLMResponse(typed, data)
	case api.MediaRequest:
		_, err = admitMediaResponse(typed, data)
	default:
		return fmt.Errorf("unsupported request type %T", request)
	}
	return err
}

func cloneObject(value map[string]any) map[string]any {
	data, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	var cloned map[string]any
	if err := json.Unmarshal(data, &cloned); err != nil {
		panic(err)
	}
	return cloned
}

func assertProtocolSchema(t *testing.T, root string) protocolSchemas {
	t.Helper()
	manifest, err := compileSchema(filepath.Join(root, "schemas", "case", "manifest.v1.schema.json"))
	if err != nil {
		t.Fatalf("compile authoritative manifest schema: %v", err)
	}
	vector, err := compileSchema(filepath.Join(root, "schemas", "case", "vector.v1.schema.json"))
	if err != nil {
		t.Fatalf("compile authoritative vector schema: %v", err)
	}
	schemaPath := filepath.Join(root, "schemas", "protocol", gatewayInvocationSchema)
	document := readJSONFile[map[string]json.RawMessage](t, schemaPath)
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
	compiler := jsonschema.NewCompiler()
	request, err := compiler.Compile(schemaPath)
	if err != nil {
		t.Fatalf("compile authoritative Gateway request schema: %v", err)
	}
	response, err := compiler.Compile(schemaPath + "#/$defs/response")
	if err != nil {
		t.Fatalf("compile authoritative Gateway response schema: %v", err)
	}
	return protocolSchemas{manifest: manifest, vector: vector, request: request, response: response}
}

func compileSchema(path string) (*jsonschema.Schema, error) {
	compiler := jsonschema.NewCompiler()
	return compiler.Compile(path)
}

func fixtureValidator(t *testing.T) admission.Validator {
	t.Helper()
	reasoningDefault := api.ThinkingAdaptive
	reasoningEffort := api.ReasoningEffortMedium
	reasoning := &ports.ModelReasoning{
		ThinkingModes: []ports.ModelThinkingMode{
			{Thinking: api.ThinkingDisabled},
			{Thinking: api.ThinkingEnabled, Efforts: []api.ReasoningEffort{api.ReasoningEffortLow, api.ReasoningEffortMedium, api.ReasoningEffortHigh}},
			{Thinking: api.ThinkingAdaptive, Efforts: []api.ReasoningEffort{api.ReasoningEffortLow, api.ReasoningEffortMedium, api.ReasoningEffortHigh}, DefaultEffort: &reasoningEffort},
		},
		DefaultThinking: &reasoningDefault,
	}
	records := []ports.ModelRecord{
		fixtureModel("chatgpt-4o", api.OperationGenerate, []api.Modality{api.ModalityText}, []api.Modality{api.ModalityText}, []api.Feature{api.FeatureToolCalls}, nil),
		fixtureModel("model.text", api.OperationGenerate, []api.Modality{api.ModalityText}, []api.Modality{api.ModalityText}, nil, nil),
		fixtureModel("model.reasoning", api.OperationGenerate, []api.Modality{api.ModalityText}, []api.Modality{api.ModalityText}, nil, reasoning),
		fixtureModel("model.tools", api.OperationGenerate, []api.Modality{api.ModalityText}, []api.Modality{api.ModalityText}, []api.Feature{api.FeatureToolCalls}, nil),
		fixtureModel("model.realtime", api.OperationRealtime, []api.Modality{api.ModalityAudio, api.ModalityText}, []api.Modality{api.ModalityText, api.ModalityAudio}, nil, nil),
		fixtureModel("model.image", api.OperationImageGeneration, []api.Modality{api.ModalityText}, []api.Modality{api.ModalityImage}, nil, nil),
		fixtureModel("model.audio", api.OperationAudioGeneration, []api.Modality{api.ModalityText}, []api.Modality{api.ModalityAudio}, nil, nil),
		fixtureModel("model.music", api.OperationMusicGeneration, []api.Modality{api.ModalityText}, []api.Modality{api.ModalityMusic}, nil, nil),
		fixtureModel("model.video", api.OperationVideoGeneration, []api.Modality{api.ModalityText}, []api.Modality{api.ModalityVideo}, nil, nil),
		fixtureModel("model.transcribe", api.OperationAudioTranscription, []api.Modality{api.ModalityAudio}, []api.Modality{api.ModalityText}, nil, nil),
	}
	catalog, err := model.NewCatalogFromRecords(records)
	if err != nil {
		t.Fatalf("construct conformance fixture catalog: %v", err)
	}
	validator := admission.New(admission.Config{
		Catalog:  catalog,
		Protocol: testkit.ProtocolCapabilities(),
		Service: testkit.ServiceCapabilities(
			"chatgpt-4o", "model.text", "model.reasoning", "model.tools", "model.realtime",
			"model.image", "model.audio", "model.music", "model.video", "model.transcribe",
		),
	})
	return validator
}

func fixtureModel(baseModel string, operation api.Operation, inputs, outputs []api.Modality, features []api.Feature, reasoning *ports.ModelReasoning) ports.ModelRecord {
	return ports.ModelRecord{BaseModel: baseModel, Lifecycle: ports.ModelLifecycleActive, Capability: ports.ModelCapability{
		Operation:        operation,
		InputModalities:  inputs,
		OutputModalities: outputs,
		Features:         features,
		Reasoning:        reasoning,
	}}
}

func admitProfileRequest(schema *jsonschema.Schema, validator admission.Validator, profile string, data []byte) (any, error) {
	if err := validateSchema(schema, data); err != nil {
		return nil, invalid("request does not satisfy the authoritative schema: %v", err)
	}
	switch profile {
	case string(api.ProfileKernelLLM):
		decoded, err := decodeLLMFrame(data)
		if err != nil {
			return api.LLMRequest{}, invalid("decode LLM request: %v", err)
		}
		admitted, err := validator.AdmitLLMFrame(decoded)
		if err != nil {
			return api.LLMRequest{}, err
		}
		return admitted.Request(), nil
	case string(api.ProfileExecutionMedia):
		decoded, err := decodeMediaFrame(data)
		if err != nil {
			return api.MediaRequest{}, invalid("decode media request: %v", err)
		}
		admitted, err := validator.AdmitMediaFrame(decoded)
		if err != nil {
			return api.MediaRequest{}, err
		}
		return admitted.Request(), nil
	default:
		return nil, invalid("unknown profile %q", profile)
	}
}

func decodeLLMFrame(data []byte) (api.LLMRequestFrame, error) {
	var request api.LLMRequest
	if err := decodeStrict(data, &request); err != nil {
		return api.LLMRequestFrame{}, err
	}
	return api.LLMRequestFrame{Request: request}, nil
}

func decodeMediaFrame(data []byte) (api.MediaRequestFrame, error) {
	var request api.MediaRequest
	if err := decodeStrict(data, &request); err != nil {
		return api.MediaRequestFrame{}, err
	}
	return api.MediaRequestFrame{Request: request}, nil
}

func operationOf(operation *api.Operation) api.Operation {
	if operation == nil {
		return ""
	}
	return *operation
}

func validateSchema(schema *jsonschema.Schema, data []byte) error {
	instance, err := jsonschema.UnmarshalJSON(bytes.NewReader(data))
	if err != nil {
		return err
	}
	return schema.Validate(instance)
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
	if response.Kind != api.ResponseKindLLM || response.SchemaVersion != gatewayInvocationVersion || response.OperationID != request.OperationID || response.Mode != request.Mode {
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
	if response.Kind != api.ResponseKindMedia || response.SchemaVersion != gatewayInvocationVersion || response.OperationID != request.OperationID || response.Mode != request.Mode {
		return response, invalid("media response envelope does not correlate with request")
	}
	switch response.Terminal.Outcome {
	case api.OutcomeSucceeded:
		if response.Terminal.Result == nil {
			return response, invalid("successful media response requires a result")
		}
		if response.Terminal.Result.Kind == api.ResultKind("") || (operationOf(request.Operation) != api.OperationAudioTranscription && response.Terminal.Result.Kind != api.ResultKind(request.Modality)) {
			return response, invalid("media result kind does not match operation")
		}
		if operationOf(request.Operation) == api.OperationAudioTranscription && response.Terminal.Result.Kind != api.ResultAudioTranscription {
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
	if o.SchemaVersion != gatewayInvocationVersion || o.OperationID != request.OperationID || o.Operation != operationOf(request.Operation) || o.Modality != request.Modality || o.Mode != request.Mode || o.Model.RequestedBaseModel != request.BaseModel || (o.Model.ResolvedBaseModel != "" && o.Model.ResolvedBaseModel != request.BaseModel) || !equalLLMInput(o.Input, request.Input) {
		return invalid("LLM observation does not correlate with request")
	}
	if response.Receipt.OperationID != request.OperationID || response.Receipt.ReceiptID == "" || response.Receipt.Revision < 1 {
		return invalid("LLM receipt is incomplete or mismatched")
	}
	return validateTiming(request.Mode, o.Timing)
}

func validateMediaObservation(request api.MediaRequest, response api.MediaResponse) error {
	o := response.Observation
	if o.SchemaVersion != gatewayInvocationVersion || o.OperationID != request.OperationID || o.Operation != operationOf(request.Operation) || o.Modality != request.Modality || o.Mode != request.Mode || o.Model.RequestedBaseModel != request.BaseModel || (o.Model.ResolvedBaseModel != "" && o.Model.ResolvedBaseModel != request.BaseModel) || !reflect.DeepEqual(o.Input, request.Input) {
		return invalid("media observation does not correlate with request")
	}
	if response.Receipt.OperationID != request.OperationID || response.Receipt.ReceiptID == "" || response.Receipt.Revision < 1 {
		return invalid("media receipt is incomplete or mismatched")
	}
	return validateTiming(request.Mode, o.Timing)
}

func equalLLMInput(left, right api.LLMInput) bool {
	leftJSON, leftErr := json.Marshal(left)
	rightJSON, rightErr := json.Marshal(right)
	if leftErr != nil || rightErr != nil {
		return false
	}
	var leftValue, rightValue any
	if json.Unmarshal(leftJSON, &leftValue) != nil || json.Unmarshal(rightJSON, &rightValue) != nil {
		return false
	}
	return reflect.DeepEqual(leftValue, rightValue)
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

func invalid(format string, args ...any) error {
	return &admissionFailure{code: "INVALID_REQUEST", cause: fmt.Errorf(format, args...)}
}

func rejectionCode(err error) string {
	var failure *admissionFailure
	if errors.As(err, &failure) {
		return failure.code
	}
	return string(admission.Code(err))
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
