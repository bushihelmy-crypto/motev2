package api

import "fmt"

// ThinkingMode is the service- and protocol-neutral way to control whether a
// model reasons and whether it may vary the amount of reasoning.  A provider
// adapter may encode these values differently (or reject a value it cannot
// express without loss).
type ThinkingMode string

const (
	ThinkingDisabled ThinkingMode = "disabled"
	ThinkingEnabled  ThinkingMode = "enabled"
	ThinkingAdaptive ThinkingMode = "adaptive"
)

// IsValid reports whether the thinking mode belongs to Gateway's public
// vocabulary.  Model/protocol/service admission remains responsible for
// deciding whether a particular combination is supported.
func (value ThinkingMode) IsValid() bool {
	switch value {
	case ThinkingDisabled, ThinkingEnabled, ThinkingAdaptive:
		return true
	default:
		return false
	}
}

// ReasoningEffort is an ordered, provider-neutral hint for how much work a
// reasoning model should spend.  It is deliberately not a token budget;
// providers may implement the same level with different token counts.
type ReasoningEffort string

const (
	ReasoningEffortMinimal ReasoningEffort = "minimal"
	ReasoningEffortLow     ReasoningEffort = "low"
	ReasoningEffortMedium  ReasoningEffort = "medium"
	ReasoningEffortHigh    ReasoningEffort = "high"
	ReasoningEffortXHigh   ReasoningEffort = "xhigh"
	ReasoningEffortMax     ReasoningEffort = "max"
)

// IsValid reports whether the effort belongs to Gateway's public vocabulary.
// "none" is intentionally absent: ThinkingDisabled is the one canonical way
// to turn reasoning off.  Adapters may map that mode to a provider's "none".
func (value ReasoningEffort) IsValid() bool {
	switch value {
	case ReasoningEffortMinimal, ReasoningEffortLow, ReasoningEffortMedium,
		ReasoningEffortHigh, ReasoningEffortXHigh, ReasoningEffortMax:
		return true
	default:
		return false
	}
}

// ReasoningConfig is the optional LLM request preference.  A nil config means
// that the caller has no preference. Gateway applies only a model-catalog
// default; otherwise it preserves omission for the upstream interface.
// ThinkingDisabled is explicit, so it must
// remain distinguishable from a nil config.
type ReasoningConfig struct {
	Thinking ThinkingMode    `json:"thinking,omitempty"`
	Effort   ReasoningEffort `json:"effort,omitempty"`
}

// Validate checks syntax and the one combination that has no meaningful
// interpretation.  Support for a valid value is checked by the selected
// model, protocol, and service owners.
func (config ReasoningConfig) Validate() error {
	if config.Thinking != "" && !config.Thinking.IsValid() {
		return fmt.Errorf("unsupported thinking mode %q", config.Thinking)
	}
	if config.Effort != "" && !config.Effort.IsValid() {
		return fmt.Errorf("unsupported reasoning effort %q", config.Effort)
	}
	if config.Thinking == ThinkingDisabled && config.Effort != "" {
		return fmt.Errorf("reasoning effort cannot be set when thinking is disabled")
	}
	if config.Thinking == "" && config.Effort == "" {
		return fmt.Errorf("reasoning config must set thinking or effort")
	}
	return nil
}
