// Package admission validates a request against the independently owned model,
// protocol, and service facts, then returns one immutable admitted request for
// the current call. It never routes, falls back, or accesses the network.
package admission
