package admission

import (
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/protocol"
	"github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/service"
)

var (
	_ protocol.LLMCall     = AdmittedLLM{}
	_ protocol.MediaCall   = AdmittedMedia{}
	_ service.LLMCall      = AdmittedLLM{}
	_ service.Call         = AdmittedMedia{}
	_ service.CallIdentity = AdmittedLLM{}
)
