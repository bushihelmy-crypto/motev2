package gateway

import (
	"context"
	"fmt"

	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/protocol"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/service"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/ports"
)

// Public aliases expose the immutable model composition values without
// defining a second public model shape.
type (
	ModelDefinition    = model.Definition
	ModelCatalog       = model.Catalog
	ModelCatalogReader = model.CatalogReader
	ModelLifecycle     = model.Lifecycle
	ModelCatalogStore  = model.CatalogStore
	ModelCatalogSource = ports.ModelCatalogSource
	ModelRecord        = ports.ModelRecord

	ProtocolDescriptor          = protocol.Descriptor
	ProtocolOperationCapability = protocol.OperationCapability
	ProtocolReasoningCapability = protocol.ReasoningCapability
	ServiceDescriptor           = service.Descriptor
	ServiceDescriptorConfig     = service.DescriptorConfig
	ServiceOperationCapability  = service.OperationCapability
	ServiceReasoningCapability  = service.ReasoningCapability

	// InvocationConfig binds the exact catalog and the already-selected protocol
	// and service descriptors before any request enters Gateway. A production
	// store may refresh between calls; Gateway never derives a permissive fallback.
	InvocationConfig struct {
		Catalog  ModelCatalogReader
		Protocol ProtocolDescriptor
		Service  ServiceDescriptor
	}
)

// NewProtocolDescriptor validates and freezes one outbound wire protocol's
// identity and capabilities at the public composition boundary.
func NewProtocolDescriptor(identifier string, operations map[api.Operation]ProtocolOperationCapability) (ProtocolDescriptor, error) {
	return protocol.NewDescriptor(identifier, operations)
}

// NewServiceDescriptor validates and freezes one selected service deployment.
func NewServiceDescriptor(config ServiceDescriptorConfig) (ServiceDescriptor, error) {
	return service.NewDescriptor(config)
}

// NewCatalogStore is the production composition boundary. It loads the
// complete database-backed catalog before admission can accept a request and
// atomically replaces it only after a later full refresh validates.
func NewCatalogStore(ctx context.Context, source ModelCatalogSource) (*ModelCatalogStore, error) {
	if source == nil {
		return nil, fmt.Errorf("model catalog source is required")
	}
	return model.NewCatalogStore(ctx, source)
}
