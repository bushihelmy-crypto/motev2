"""The graph node that requests and resumes authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.config import AuthorizeBinding
from mote_kernel.act.contract import (
    ActContractError,
    ActHookCommand,
    ActHookEnvelope,
    AuthorizedInvocation,
    AuthorizeNodeInput,
    AuthorizeStageValue,
    Deny,
    HookStateProjection,
    InitialAuthorization,
    ResolveStageValue,
    ResumedAuthorization,
)
from mote_kernel.act.identity import ActHookStage, OpaqueGraphFailureReason
from mote_kernel.act.port import AuthorizePort
from mote_kernel.config import ConfigActivation
from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue
from mote_kernel.state.graph_state import GraphNodeId

HookStateT = TypeVar("HookStateT", bound=HookStateProjection)
HookCommandT = TypeVar("HookCommandT", bound=ActHookCommand)


@dataclass(frozen=True, slots=True)
class AuthorizeNode(Generic[HookStateT, HookCommandT]):
    """Request authorization once, then continue only for an ``Allow``."""

    authorize_port: AuthorizePort
    failure_reason: OpaqueGraphFailureReason
    admission: ActPayloadAdmission[HookStateT, HookCommandT]

    async def __call__(
        self,
        activation: ConfigActivation[AuthorizeNodeInput[HookCommandT]],
        /,
    ) -> HookActivationRequest[ActHookEnvelope, HookStateT] | Graph.Outcome[HookGraphValue]:
        value = activation.value
        hook_result = self.admission.admit_hook_result(value.hook_result)
        config = activation.activation_config
        authorize_port = self.authorize_port
        failure_reason = self.failure_reason
        if config is not None:
            selected = config.bind(AuthorizeBinding[HookStateT, HookCommandT]())
            if selected.admission != self.admission:
                raise ActContractError("Act config binding changed the compiled payload contract")
            authorize_port = selected.capability.port
            failure_reason = selected.capability.failure_reason
        envelope = hook_result.value
        if envelope.stage is not ActHookStage.RESOLVE or type(envelope.payload) is not ResolveStageValue:
            raise ActContractError("authorize input must be a Resolve Hook envelope")
        authorization = envelope.payload.authorization
        self.admission.admit_authorization_input(authorization)

        if type(authorization.phase) is InitialAuthorization:
            resolved = authorization.phase.resolved
            request_ref = self.admission.admit_authorization_request_ref(
                await authorize_port.request_authorization(resolved)
            )
            if request_ref.pairing != resolved.request.pairing:
                raise ActContractError("authorization request pairing does not match resolved invocation")
            interrupt_payload = self.admission.admit_interrupt_payload(authorize_port.encode_interrupt(request_ref))
            return Graph.interrupt(interrupt_payload)

        resumed = cast(ResumedAuthorization, authorization.phase)
        self.admission.admit_authorization_decision(resumed.decision)
        if type(resumed.decision) is Deny:
            return Graph.failure(failure_reason.value)

        invocation = AuthorizedInvocation(resumed.resolved)
        self.admission.admit_authorized_invocation(invocation)
        hook_state = self.admission.admit_hook_state(envelope.hook_state)
        next_envelope = ActHookEnvelope(
            ActHookStage.AUTHORIZE,
            AuthorizeStageValue(invocation),
            hook_state,
        )
        hook_request = HookActivationRequest(next_envelope, hook_state, GraphNodeId("authorize"))
        self.admission.admit_hook_request(hook_request)
        return hook_request


__all__ = ["AuthorizeNode"]
