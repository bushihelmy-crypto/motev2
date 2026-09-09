"""The graph node that requests and resumes authorization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar, cast

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.config import AuthorizeBinding
from mote_kernel.act.contract import (
    ActContractError,
    ActHookCommand,
    ActHookEnvelope,
    AuthorizedInvocation,
    AuthorizeStageValue,
    Deny,
    HookStateProjection,
    InitialAuthorization,
    ResolveStageValue,
    ResumedAuthorization,
)
from mote_kernel.act.failover import ActFailoverDecorators, FailoverPortDecorator, normalize_act_failover_decorators
from mote_kernel.act.identity import ActHookStage, ActNodeId, OpaqueGraphFailureReason
from mote_kernel.act.port import (
    AuthorizeCodecBinding,
    AuthorizeCodecCapture,
    AuthorizePort,
    capture_authorize_port_binding,
    capture_authorize_port_contract,
)
from mote_kernel.config import ConfigActivation, ConfigSnapshotKey
from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue, HookResult
from mote_kernel.state.graph_state import GraphNodeId

HookStateT = TypeVar("HookStateT", bound=HookStateProjection)
HookCommandT = TypeVar("HookCommandT", bound=ActHookCommand)


@dataclass(frozen=True, slots=True)
class AuthorizeNode(Generic[HookStateT, HookCommandT]):
    """Request authorization once, then continue only for an ``Allow``."""

    authorize_port: AuthorizePort
    failure_reason: OpaqueGraphFailureReason
    admission: ActPayloadAdmission[HookStateT, HookCommandT]
    failover: ActFailoverDecorators | FailoverPortDecorator | None = None
    assembly_snapshot_key: ConfigSnapshotKey | None = field(default=None, kw_only=True, repr=False, compare=False)
    _codec_binding: AuthorizeCodecBinding = field(init=False, repr=False, compare=False)
    _codec_capture: AuthorizeCodecCapture = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        decorators = normalize_act_failover_decorators(self.failover)
        port = decorators.authorize_port(self.authorize_port)
        codec_capture = capture_authorize_port_binding(port)
        codec_binding = codec_capture.binding
        object.__setattr__(self, "authorize_port", port)
        object.__setattr__(self, "failover", decorators)
        object.__setattr__(self, "_codec_binding", codec_binding)
        object.__setattr__(self, "_codec_capture", codec_capture)
        if self.assembly_snapshot_key is not None and type(self.assembly_snapshot_key) is not ConfigSnapshotKey:
            raise ActContractError("authorize assembly snapshot key is malformed")

    async def __call__(
        self,
        activation: ConfigActivation[HookResult[ActHookEnvelope, HookCommandT]],
        /,
    ) -> HookActivationRequest[ActHookEnvelope, HookStateT] | Graph.Outcome[HookGraphValue]:
        hook_result = self.admission.admit_hook_result(activation.value)
        config = activation.activation_config
        decorators = normalize_act_failover_decorators(self.failover)
        authorize_port = self.authorize_port
        failure_reason = self.failure_reason
        if config is not None:
            selected = config.bind(AuthorizeBinding[HookStateT, HookCommandT]())
            if selected.admission != self.admission:
                raise ActContractError("Act config binding changed the compiled payload contract")
            if self.assembly_snapshot_key is None or selected.snapshot_key != self.assembly_snapshot_key:
                authorize_port = decorators.authorize_port(selected.capability.port)
                # A newly selected revision may replace the codec provider;
                # admit its complete surface once before using it.
                capture_authorize_port_contract(authorize_port)
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
        hook_request = HookActivationRequest(
            next_envelope,
            hook_state,
            GraphNodeId(str(ActNodeId.AUTHORIZE)),
        )
        self.admission.admit_hook_request(hook_request)
        return hook_request

    @property
    def codec_binding(self) -> AuthorizeCodecBinding:
        """Return the codec identity captured for this stage's Port."""

        return self._codec_binding

    @property
    def codec_capture(self) -> AuthorizeCodecCapture:
        """Return the provenance used to avoid a second metadata read."""

        return self._codec_capture


__all__ = ["AuthorizeNode"]
