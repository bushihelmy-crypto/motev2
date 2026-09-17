// Package gateway is the public construction and invocation surface for the
// Mote model gateway. Concrete DTOs remain in api, orchestration stays
// internal, and the repository conformance contract owns cross-language
// behavior. Inbound framing and JSON decoding belong to mote-infra/invocation.
package gateway
