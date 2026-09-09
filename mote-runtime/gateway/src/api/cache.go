package api

// CacheState is deliberately richer than a boolean: a provider can omit a
// cache report, and a caller can explicitly bypass a cache.
type CacheState string

const (
	CacheNotRequested CacheState = "not_requested"
	CacheHit          CacheState = "hit"
	CacheMiss         CacheState = "miss"
	CacheBypassed     CacheState = "bypassed"
	CacheUnavailable  CacheState = "unavailable"
)

// PromptCacheObservation contains normalized provider prompt/context-cache
// facts.  KeyFingerprint is opaque and must never be a raw cache key.
type PromptCacheObservation struct {
	State          CacheState `json:"state"`
	ReadTokens     *int64     `json:"read_tokens,omitempty"`
	WriteTokens    *int64     `json:"write_tokens,omitempty"`
	KeyFingerprint string     `json:"key_fingerprint,omitempty"`
}

// ResultCacheObservation describes exact-result cache coordination.  Partial
// streams and unknown outcomes are never cacheable.
type ResultCacheObservation struct {
	State          CacheState `json:"state"`
	KeyFingerprint string     `json:"key_fingerprint,omitempty"`
}

// CacheObservation is included once in the terminal response, after cache
// accounting has been finalized.
type CacheObservation struct {
	Prompt PromptCacheObservation `json:"prompt"`
	Result ResultCacheObservation `json:"result"`
}
