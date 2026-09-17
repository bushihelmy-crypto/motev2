package admission

import (
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/api"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/model"
)

// AdmittedLLM is the one-call immutable result of admission. It is created
// only by Validator after the model, protocol, service, request shape, and
// parameter normalization have all succeeded. It is not a queue record and
// carries no fallback candidate.
type AdmittedLLM struct {
	request     api.LLMRequest
	model       model.Definition
	protocolID  string
	serviceKind string
}

func newAdmittedLLM(request api.LLMRequest, definition model.Definition, protocolID, serviceKind string) AdmittedLLM {
	return AdmittedLLM{
		request:     request,
		model:       definition,
		protocolID:  protocolID,
		serviceKind: serviceKind,
	}
}

// Request returns the normalized request. Callers must treat it as read-only.
func (admitted AdmittedLLM) Request() api.LLMRequest { return admitted.request }

// Model returns the exact immutable model definition used during admission.
func (admitted AdmittedLLM) Model() model.Definition { return admitted.model }

// BaseModel returns the exact model identity selected by Router.
func (admitted AdmittedLLM) BaseModel() string { return admitted.model.BaseModel() }

// ProtocolID returns the protocol identity frozen for this call.
func (admitted AdmittedLLM) ProtocolID() string { return admitted.protocolID }

// ServiceKind returns the service identity frozen for this call.
func (admitted AdmittedLLM) ServiceKind() string { return admitted.serviceKind }

// AdmittedMedia is the media counterpart of AdmittedLLM.
type AdmittedMedia struct {
	request     api.MediaRequest
	model       model.Definition
	protocolID  string
	serviceKind string
}

func newAdmittedMedia(request api.MediaRequest, definition model.Definition, protocolID, serviceKind string) AdmittedMedia {
	return AdmittedMedia{
		request:     request,
		model:       definition,
		protocolID:  protocolID,
		serviceKind: serviceKind,
	}
}

// Request returns the normalized media request. Callers must treat it as read-only.
func (admitted AdmittedMedia) Request() api.MediaRequest { return admitted.request }

// Model returns the exact immutable model definition used during admission.
func (admitted AdmittedMedia) Model() model.Definition { return admitted.model }

// BaseModel returns the exact model identity selected by Router.
func (admitted AdmittedMedia) BaseModel() string { return admitted.model.BaseModel() }

// ProtocolID returns the protocol identity frozen for this call.
func (admitted AdmittedMedia) ProtocolID() string { return admitted.protocolID }

// ServiceKind returns the service identity frozen for this call.
func (admitted AdmittedMedia) ServiceKind() string { return admitted.serviceKind }
