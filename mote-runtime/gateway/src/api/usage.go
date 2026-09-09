package api

// UsageAvailability describes whether the provider reported a quantity and
// whether Gateway had to estimate it.  Pointer fields in Usage preserve the
// distinction between an absent quantity and a real zero.
type UsageAvailability string

const (
	UsageReported    UsageAvailability = "reported"
	UsageEstimated   UsageAvailability = "estimated"
	UsagePartial     UsageAvailability = "partial"
	UsageUnavailable UsageAvailability = "unavailable"
)

// Usage is the normalized terminal usage snapshot used by Kernel billing and
// Langfuse projection.  It contains no per-chunk values.
type Usage struct {
	Availability      UsageAvailability `json:"availability"`
	InputTokens       *int64            `json:"input_tokens,omitempty"`
	OutputTokens      *int64            `json:"output_tokens,omitempty"`
	TotalTokens       *int64            `json:"total_tokens,omitempty"`
	CacheReadTokens   *int64            `json:"cache_read_tokens,omitempty"`
	CacheWriteTokens  *int64            `json:"cache_write_tokens,omitempty"`
	ReasoningTokens   *int64            `json:"reasoning_tokens,omitempty"`
	InputTextTokens   *int64            `json:"input_text_tokens,omitempty"`
	OutputTextTokens  *int64            `json:"output_text_tokens,omitempty"`
	InputAudioTokens  *int64            `json:"input_audio_tokens,omitempty"`
	OutputAudioTokens *int64            `json:"output_audio_tokens,omitempty"`
	InputImageTokens  *int64            `json:"input_image_tokens,omitempty"`
	OutputImageTokens *int64            `json:"output_image_tokens,omitempty"`
	InputVideoTokens  *int64            `json:"input_video_tokens,omitempty"`
	OutputVideoTokens *int64            `json:"output_video_tokens,omitempty"`
}
