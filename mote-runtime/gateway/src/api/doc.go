// Package api owns the provider-neutral request, terminal result, usage,
// observation, receipt, and error shapes exposed by the model gateway.
//
// LLMRequest/LLMResponse are the Kernel profile. MediaRequest/MediaResponse
// are the Execution profile and are intentionally different nominal DTOs.
// Streaming delivery events are local caller-owned transport values; they are
// folded into one final result before a terminal response crosses either
// runtime boundary.
package api
