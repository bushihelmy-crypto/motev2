// Package gateway is the public construction surface for the Mote model
// gateway. Concrete request DTOs remain in api; orchestration stays internal.
// Cross-language invocation behavior is owned by the root conformance
// contract, not by this package's Go method names. The terminal response is
// final-only; live stream delivery is owned by the invocation handle.
package gateway
