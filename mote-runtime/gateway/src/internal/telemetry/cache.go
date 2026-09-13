package telemetry

// This file owns prompt-cache and result-cache instruments. It must never use
// credentials, cache keys, request IDs, or arbitrary endpoints as metric labels.
