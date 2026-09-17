// Package service owns configured upstream service identity, target resolution,
// authorization, cloud signing, exact-model deployability, and service
// capability declarations. Concrete connectors live in this owner tree; there
// is no global runtime registry.
// Connector call views preserve the admitted model/protocol/service identity;
// protocol body encoding stays in internal/protocol.
package service
