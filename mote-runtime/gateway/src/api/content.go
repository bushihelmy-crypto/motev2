package api

import (
	"encoding/json"
	"fmt"
	"unicode/utf8"
)

// ArtifactRef identifies bytes owned by an artifact/content service. The
// reference is opaque; it is not a filesystem path, signed URL, credential,
// or inline media payload.
type ArtifactRef struct {
	ArtifactID     string `json:"artifact_id"`
	Revision       int64  `json:"revision"`
	Representation string `json:"representation"`
	Kind           string `json:"kind"`
	MIMEType       string `json:"mime_type"`
	ContentRef     string `json:"content_ref"`
	Digest         string `json:"digest"`
	Size           int64  `json:"size"`
}

// Artifact kinds are the only durable media vocabulary accepted at the
// Gateway boundary.  A kind has one semantic modality; callers must not make
// Gateway infer it from MIME types or provider-specific labels.
const (
	ArtifactKindText  = "text"
	ArtifactKindImage = "image"
	ArtifactKindAudio = "audio"
	ArtifactKindMusic = "music"
	ArtifactKindVideo = "video"
)

// ArtifactModality resolves the canonical modality owned by an artifact kind.
// The boolean is false for an unknown kind; admission rejects it before an
// adapter or content service is contacted.
func ArtifactModality(kind string) (Modality, bool) {
	modality, ok := artifactModalities[kind]
	return modality, ok
}

var artifactModalities = map[string]Modality{
	ArtifactKindText:  ModalityText,
	ArtifactKindImage: ModalityImage,
	ArtifactKindAudio: ModalityAudio,
	ArtifactKindMusic: ModalityMusic,
	ArtifactKindVideo: ModalityVideo,
}

// ContentPart is a service- and protocol-neutral LLM message part. Media is always carried
// by an ArtifactRef so the Kernel request remains bounded and replayable.
type ContentPart struct {
	Type     string       `json:"type"`
	Text     string       `json:"text,omitempty"`
	Artifact *ArtifactRef `json:"artifact,omitempty"`
}

// Message is one model conversation message. Tool calls here are complete
// model messages; Gateway never discovers or executes a tool.
type Message struct {
	Role       string        `json:"role"`
	Content    []ContentPart `json:"content"`
	Name       string        `json:"name,omitempty"`
	ToolCallID string        `json:"tool_call_id,omitempty"`
	ToolCalls  []ToolCall    `json:"tool_calls,omitempty"`
}

// ResponseFormat requests a schema-constrained response. The Gateway carries
// the caller-owned schema through; strict success requires compatible model,
// protocol, and service owners and is not implied by ordinary JSON output.
type ResponseFormat struct {
	Type   string          `json:"type"`
	Schema json.RawMessage `json:"schema,omitempty"`
}

// GenerationParameters contains controls shared by LLM and media adapters.
// Protocol-specific knobs require a reviewed protocol version instead of an
// unbounded map at this boundary.
type GenerationParameters struct {
	Temperature     *float64 `json:"temperature,omitempty"`
	TopP            *float64 `json:"top_p,omitempty"`
	MaxOutputTokens *int64   `json:"max_output_tokens,omitempty"`
	Stop            []string `json:"stop,omitempty"`
	Seed            *int64   `json:"seed,omitempty"`
}

// Validate enforces the service- and protocol-neutral parameter domain shared
// by typed invocation frames and normalized observations. Model catalog
// defaults and clamp boundaries reuse this method so they cannot emit values
// outside the public contract.
func (parameters GenerationParameters) Validate() error {
	if parameters.Temperature != nil {
		value := *parameters.Temperature
		if !(value >= 0 && value <= 2) {
			return fmt.Errorf("temperature must be finite and between 0 and 2")
		}
	}
	if parameters.TopP != nil {
		value := *parameters.TopP
		if !(value > 0 && value <= 1) {
			return fmt.Errorf("top_p must be finite, greater than 0, and at most 1")
		}
	}
	if parameters.MaxOutputTokens != nil && *parameters.MaxOutputTokens < 1 {
		return fmt.Errorf("max_output_tokens must be positive")
	}
	return validateStopSequences(parameters.Stop)
}

func validateStopSequences(sequences []string) error {
	if len(sequences) > 16 {
		return fmt.Errorf("stop must contain at most 16 sequences")
	}
	for index, sequence := range sequences {
		length := utf8.RuneCountInString(sequence)
		if !utf8.ValidString(sequence) || length < 1 || length > 256 {
			return fmt.Errorf("stop[%d] must be valid UTF-8 with 1-256 characters", index)
		}
	}
	return nil
}
