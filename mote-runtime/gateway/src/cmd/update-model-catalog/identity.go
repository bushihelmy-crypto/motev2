package main

import (
	"strings"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
)

var supportedModes = map[string]api.Operation{
	"chat":                api.OperationGenerate,
	"completion":          api.OperationGenerate,
	"responses":           api.OperationGenerate,
	"embedding":           api.OperationEmbedding,
	"rerank":              api.OperationRerank,
	"image_generation":    api.OperationImageGeneration,
	"image_edit":          api.OperationImageGeneration,
	"audio_speech":        api.OperationAudioGeneration,
	"audio_transcription": api.OperationAudioTranscription,
	"video_generation":    api.OperationVideoGeneration,
	"realtime":            api.OperationRealtime,
}

func isBatchRecord(ref recordRef) bool {
	return strings.HasSuffix(strings.ToLower(strings.TrimSpace(ref.key)), ":batch") ||
		strings.HasSuffix(strings.ToLower(strings.TrimSpace(ref.record.BaseModel)), ":batch")
}

func syntheticModelID(modelID string) bool {
	value := strings.ToLower(modelID)
	if strings.HasSuffix(value, ":batch") || syntheticRequestPreset(value) {
		return true
	}
	hasSuffix := func(values ...string) bool {
		for _, suffix := range values {
			if strings.HasSuffix(value, "-"+suffix) {
				return true
			}
		}
		return false
	}
	if value == "codex-auto-review" {
		return true
	}
	if (strings.HasPrefix(value, "claude-") || strings.Contains(value, "/claude-")) &&
		hasSuffix("thinking", "max", "xhigh", "high", "medium", "low") {
		return true
	}
	if strings.HasPrefix(value, "deepseek-v4-") && hasSuffix("none", "max") {
		return true
	}
	if value == "o3-mini-high" || value == "o3-mini-medium" || value == "o3-mini-low" ||
		value == "o3-mini-2025-01-31-high" || value == "o3-mini-2025-01-31-medium" || value == "o3-mini-2025-01-31-low" {
		return true
	}
	return (strings.HasPrefix(value, "grok-") && strings.HasSuffix(value, "-search")) ||
		value == "grok-3-mini-high" || value == "grok-3-mini-low"
}

func syntheticRequestPreset(value string) bool {
	if strings.HasPrefix(value, "preset/") {
		return true
	}
	switch value {
	case "conservative", "creative", "edit", "erase", "fast", "inpaint", "outpaint",
		"sketch", "structure", "style", "style-transfer", "remove-background",
		"replace-background-and-relight", "search-and-recolor", "search-and-replace":
		return true
	default:
		return false
	}
}
