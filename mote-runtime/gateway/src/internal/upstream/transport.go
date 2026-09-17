package upstream

import "context"

// Transport owns outbound network mechanics. It does not select models,
// protocols, services, or semantic retry candidates.
type Transport interface {
	Do(context.Context, AuthorizedRequest) (Response, error)
}
