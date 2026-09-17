package api

// Operation is the service- and protocol-neutral semantic operation requested
// from a model. It deliberately describes what the model does, not which service
// endpoint happens to implement it.
type Operation string

const (
	OperationGenerate           Operation = "generate"
	OperationEmbedding          Operation = "embedding"
	OperationRerank             Operation = "rerank"
	OperationImageGeneration    Operation = "image_generation"
	OperationAudioGeneration    Operation = "audio_generation"
	OperationAudioTranscription Operation = "audio_transcription"
	OperationMusicGeneration    Operation = "music_generation"
	OperationVideoGeneration    Operation = "video_generation"
	OperationRealtime           Operation = "realtime"
)

// PrimaryModality returns the canonical top-level modality for an operation.
// Callers do not send this value; admission derives it from the selected
// operation. Nested input content can still contribute additional input
// modalities during admission.
func (value Operation) PrimaryModality() (Modality, bool) {
	switch value {
	case OperationGenerate:
		return ModalityText, true
	case OperationRealtime, OperationAudioGeneration, OperationAudioTranscription:
		return ModalityAudio, true
	case OperationImageGeneration:
		return ModalityImage, true
	case OperationMusicGeneration:
		return ModalityMusic, true
	case OperationVideoGeneration:
		return ModalityVideo, true
	default:
		return "", false
	}
}

// Profile identifies the caller-owned contract at the Gateway boundary. A
// profile is intentionally narrower than Gateway's internal capability
// catalog: Kernel uses kernel_llm, while Execution uses execution_media for
// media generation and understanding.
type Profile string

const (
	ProfileKernelLLM      Profile = "kernel_llm"
	ProfileExecutionMedia Profile = "execution_media"
)

// RequestKind is the wire discriminator for the two current request DTOs.
type RequestKind string

const (
	RequestKindLLM   RequestKind = "llm"
	RequestKindMedia RequestKind = "media"
)

// ResponseKind is the wire discriminator for terminal DTOs.
type ResponseKind string

const (
	ResponseKindLLM   ResponseKind = "llm_response"
	ResponseKindMedia ResponseKind = "media_response"
)

// Modality identifies a model input or output medium. A request's top-level
// modality is its primary operation/result medium; nested typed content still
// contributes its actual input modalities during admission. Music remains
// distinct from generic audio so a connector cannot silently collapse it.
type Modality string

const (
	ModalityText      Modality = "text"
	ModalityImage     Modality = "image"
	ModalityAudio     Modality = "audio"
	ModalityMusic     Modality = "music"
	ModalityVideo     Modality = "video"
	ModalityEmbedding Modality = "embedding"
)

// DeliveryMode selects the invocation lifecycle.  The mode does not change
// the semantic result: a server stream is finalized into the same terminal
// result shape as a unary call.
type DeliveryMode string

const (
	ModeUnary        DeliveryMode = "unary"
	ModeServerStream DeliveryMode = "server_stream"
	ModeDuplex       DeliveryMode = "duplex"
	ModeAsync        DeliveryMode = "async"
)

// Feature is a required invocation feature, not a routing preference. A
// catalog entry may publish model-side compatibility evidence, but admission
// must still intersect it with the selected protocol and service capabilities.
type Feature string

const (
	FeatureToolCalls Feature = "tool_calls"
	// FeatureStructured requests schema-constrained JSON. The model catalog may
	// retain model compatibility evidence for it, but this bit alone never
	// promises that a selected protocol/service path enforces the schema.
	FeatureStructured  Feature = "structured_output"
	FeaturePromptCache Feature = "prompt_cache"
	FeatureUsage       Feature = "usage"
)

// Outcome is the terminal state returned by Gateway.  Unknown is kept
// distinct from failed because the upstream may have accepted the operation
// even when the response was lost.
type Outcome string

const (
	OutcomeSucceeded Outcome = "succeeded"
	OutcomeSubmitted Outcome = "submitted"
	OutcomeFailed    Outcome = "failed"
	OutcomeCancelled Outcome = "cancelled"
	OutcomeUnknown   Outcome = "unknown"
)

// ResultKind identifies the canonical terminal result union.
type ResultKind string

const (
	ResultGenerate           ResultKind = "generate"
	ResultImage              ResultKind = "image"
	ResultAudio              ResultKind = "audio"
	ResultMusic              ResultKind = "music"
	ResultVideo              ResultKind = "video"
	ResultEmbedding          ResultKind = "embedding"
	ResultRerank             ResultKind = "rerank"
	ResultAudioTranscription ResultKind = "audio_transcription"
	ResultRealtime           ResultKind = "realtime"
)

// FinishReason is the normalized reason why a model response stopped.
type FinishReason string

const (
	FinishStop          FinishReason = "stop"
	FinishLength        FinishReason = "length"
	FinishToolCalls     FinishReason = "tool_calls"
	FinishContentFilter FinishReason = "content_filter"
	FinishCompleted     FinishReason = "completed"
	FinishCancelled     FinishReason = "cancelled"
	FinishError         FinishReason = "error"
	FinishUnknown       FinishReason = "unknown"
)

// IsValid reports whether the operation belongs to Gateway's known capability
// vocabulary. Profile admission remains stricter: embedding and rerank are
// reserved for a future versioned boundary.
func (value Operation) IsValid() bool {
	switch value {
	case OperationGenerate, OperationEmbedding, OperationRerank,
		OperationImageGeneration, OperationAudioGeneration,
		OperationAudioTranscription, OperationMusicGeneration,
		OperationVideoGeneration, OperationRealtime:
		return true
	default:
		return false
	}
}

// IsValid reports whether the modality belongs to Gateway's known capability
// vocabulary.
func (value Modality) IsValid() bool {
	switch value {
	case ModalityText, ModalityImage, ModalityAudio, ModalityMusic,
		ModalityVideo, ModalityEmbedding:
		return true
	default:
		return false
	}
}

// IsValid reports whether the delivery mode belongs to the known lifecycle
// vocabulary.
func (value DeliveryMode) IsValid() bool {
	switch value {
	case ModeUnary, ModeServerStream, ModeDuplex, ModeAsync:
		return true
	default:
		return false
	}
}

// IsValid reports whether the feature belongs to the service- and
// protocol-neutral capability vocabulary.
func (value Feature) IsValid() bool {
	switch value {
	case FeatureToolCalls, FeatureStructured, FeaturePromptCache, FeatureUsage:
		return true
	default:
		return false
	}
}

// IsLLM reports whether an operation belongs to the Kernel LLM profile.
func (value Operation) IsLLM() bool {
	return value == OperationGenerate || value == OperationRealtime
}

// IsMedia reports whether an operation belongs to the Execution media
// profile. Embedding and reranking remain separate future profiles rather than
// being smuggled into either boundary.
func (value Operation) IsMedia() bool {
	switch value {
	case OperationImageGeneration, OperationAudioGeneration,
		OperationAudioTranscription, OperationMusicGeneration,
		OperationVideoGeneration:
		return true
	default:
		return false
	}
}
