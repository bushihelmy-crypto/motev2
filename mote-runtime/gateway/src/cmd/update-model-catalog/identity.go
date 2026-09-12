package main

import (
	"sort"
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

// identityIndex owns the one catalog-ID rule: every slash-qualified source ID
// becomes its final path segment, and equal segments are deduplicated without
// consulting provider or BaseModel metadata.
type identityIndex struct {
	canonical map[string]string
}

func buildIdentityIndex(records []recordRef, newModels map[string]struct{}) identityIndex {
	candidates := make(map[string][]string)
	add := func(value string) {
		value = modelIDLeaf(value)
		if value == "" {
			return
		}
		folded := strings.ToLower(value)
		for _, existing := range candidates[folded] {
			if existing == value {
				return
			}
		}
		candidates[folded] = append(candidates[folded], value)
	}
	for _, ref := range records {
		key := strings.TrimSpace(ref.key)
		if key == "" || nonModelSourceRecord(key) || (syntheticModelID(key) && !strings.HasSuffix(strings.ToLower(key), ":batch")) {
			continue
		}
		add(key)
	}
	for modelID := range newModels {
		if strings.TrimSpace(modelID) == "" || syntheticModelID(modelID) {
			continue
		}
		add(modelID)
	}
	canonical := make(map[string]string, len(candidates))
	for folded, values := range candidates {
		sort.Slice(values, func(left, right int) bool {
			leftLower := values[left] == strings.ToLower(values[left])
			rightLower := values[right] == strings.ToLower(values[right])
			if leftLower != rightLower {
				return leftLower
			}
			return values[left] < values[right]
		})
		canonical[folded] = values[0]
	}
	return identityIndex{canonical: canonical}
}

func stripBatchSuffix(value string) string {
	if strings.HasSuffix(strings.ToLower(value), ":batch") {
		return value[:len(value)-len(":batch")]
	}
	return value
}

func modelIDLeaf(value string) string {
	value = stripBatchSuffix(strings.TrimSpace(value))
	if slash := strings.LastIndexByte(value, '/'); slash >= 0 {
		value = value[slash+1:]
	}
	return value
}

func canonicalRecordID(ref recordRef, index identityIndex) string {
	leaf := modelIDLeaf(ref.key)
	if leaf == "" {
		return ""
	}
	if canonical, ok := index.canonical[strings.ToLower(leaf)]; ok {
		return canonical
	}
	return leaf
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

func inferOperation(modelID string) api.Operation {
	value := strings.ToLower(modelID)
	switch {
	case strings.Contains(value, "moderation"):
		return ""
	case value == "flux":
		// A stripped leaf cannot distinguish Deepgram's transcription model
		// from image models with the same generic name. Without a source row,
		// publishing either operation would invent an identity fact.
		return ""
	case strings.Contains(value, "rerank"):
		return api.OperationRerank
	case strings.Contains(value, "embedding"), strings.Contains(value, "embed-"),
		strings.Contains(value, "bge-"), strings.Contains(value, "m3e-"), strings.Contains(value, "jina-clip"):
		return api.OperationEmbedding
	case strings.Contains(value, "transcribe"), strings.Contains(value, "whisper"),
		strings.Contains(value, "sensevoice"), strings.Contains(value, "parakeet"),
		strings.Contains(value, "-asr"), strings.Contains(value, "-stt"),
		strings.Contains(value, "voxtral-mini-realtime"):
		return api.OperationAudioTranscription
	case strings.Contains(value, "lyria"), strings.Contains(value, "musicgen"), strings.Contains(value, "suno"):
		return api.OperationMusicGeneration
	case strings.Contains(value, "tts"), strings.HasPrefix(value, "speech-"),
		strings.Contains(value, "text-to-speech"), strings.Contains(value, "text2speech"),
		strings.Contains(value, "melotts"), strings.Contains(value, "orpheus"),
		strings.Contains(value, "eleven-"):
		return api.OperationAudioGeneration
	case strings.Contains(value, "sora"), strings.Contains(value, "veo-"), strings.Contains(value, "seedance"),
		strings.Contains(value, "imagine-video"), strings.Contains(value, "video-01"),
		strings.Contains(value, "wan-video"), strings.Contains(value, "wan-") &&
			(strings.Contains(value, "-t2v") || strings.Contains(value, "-i2v")),
		strings.Contains(value, "kling"),
		strings.Contains(value, "hailuo"), strings.Contains(value, "pixverse"),
		strings.Contains(value, "gen3a"), strings.Contains(value, "gen4-turbo"),
		strings.Contains(value, "gen4-aleph"), strings.Contains(value, "ray-"),
		strings.Contains(value, "motion-"), strings.Contains(value, "inkling"), strings.Contains(value, "vidu"):
		return api.OperationVideoGeneration
	case imageModelName(value):
		return api.OperationImageGeneration
	case realtimeModel(value):
		return api.OperationRealtime
	default:
		return api.OperationGenerate
	}
}

func imageModelName(value string) bool {
	return strings.Contains(value, "dall-e") || strings.Contains(value, "gpt-image") ||
		strings.Contains(value, "chatgpt-image") || strings.Contains(value, "imagen-") ||
		strings.Contains(value, "imagen4") || strings.Contains(value, "seedream") || strings.Contains(value, "imagine-image") ||
		strings.Contains(value, "image-01") || strings.Contains(value, "flux") ||
		strings.Contains(value, "sdxl") || strings.Contains(value, "instantid") ||
		strings.Contains(value, "jimeng") || strings.Contains(value, "text-to-image") ||
		strings.Contains(value, "text2image") || strings.Contains(value, "imagegeneration") ||
		strings.HasSuffix(value, "-image") || strings.Contains(value, "-image-") ||
		strings.Contains(value, "flash-image") || strings.Contains(value, "mai-image") ||
		strings.Contains(value, "nano-banana") || strings.Contains(value, "qwen-image") ||
		strings.Contains(value, "qwen-edit") || strings.Contains(value, "ideogram") ||
		strings.Contains(value, "recraft") || strings.Contains(value, "playground-v") ||
		strings.Contains(value, "openjourney") || strings.Contains(value, "analog-diffusion") ||
		strings.Contains(value, "realistic_vision") || strings.Contains(value, "stable-diffusion") ||
		strings.Contains(value, "stable-image") || strings.Contains(value, "stable-outpaint") ||
		strings.Contains(value, "stable-style") || strings.Contains(value, "stable-control") ||
		strings.Contains(value, "stable-erase") || strings.Contains(value, "stable-remove") ||
		strings.Contains(value, "stable-search") || strings.Contains(value, "stable-creative") ||
		strings.Contains(value, "stable-conservative") || strings.Contains(value, "stable-fast") ||
		strings.Contains(value, "stable-inpaint") || strings.Contains(value, "stable_") ||
		strings.Contains(value, "sd3") || strings.Contains(value, "ssd-1b") ||
		strings.Contains(value, "titan-image") || strings.Contains(value, "nova-canvas") ||
		strings.Contains(value, "p-image") || strings.Contains(value, "dreamina") ||
		strings.Contains(value, "hidream") ||
		strings.Contains(value, "wan2.6-image") || strings.Contains(value, "gen4-image") ||
		strings.Contains(value, "image-upscale")
}

func realtimeModel(modelID string) bool {
	value := strings.ToLower(modelID)
	return strings.Contains(value, "realtime") || strings.Contains(value, "native-audio") || strings.Contains(value, "-live-")
}
