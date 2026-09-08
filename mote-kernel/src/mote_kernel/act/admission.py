"""Fixed structural admission for Act's outer DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.act.contract import (
    ActContractError,
    ActHookCommand,
    ActHookEnvelope,
    ActRequest,
    ActSlotId,
    Allow,
    AuthorizationDecision,
    AuthorizationInput,
    AuthorizationInterruptView,
    AuthorizationPhase,
    AuthorizationRequestRef,
    AuthorizedInvocation,
    AuthorizeStageValue,
    Deny,
    ExecutePortResult,
    ExecuteStageValue,
    ExecutionStopped,
    HookStateProjection,
    InitialAuthorization,
    ResolutionStopped,
    ResolvedInvocation,
    ResolvePortResult,
    ResolveStageValue,
    ResumedAuthorization,
    SettledActResult,
    SettlementProjection,
    SettleStageValue,
    ToolExchangeWriteRequest,
    ToolExchangeWriteResult,
    ToolExecutionResult,
    admit_authorization_interrupt_payload,
)
from mote_kernel.act.identity import ActHookStage, ActNodeId, OpaqueGraphFailureReason
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue, HookResult, HookStageResult
from mote_kernel.state.graph_state import GraphNodeId

AdmissionValueT = TypeVar("AdmissionValueT")
HookStateT = TypeVar("HookStateT", bound=HookStateProjection)
HookCommandT = TypeVar("HookCommandT", bound=ActHookCommand)


def _exact(value: AdmissionValueT, expected: type[AdmissionValueT], field: str, /) -> None:
    if type(value) is not expected:
        raise ActContractError(f"{field} must be an exact {expected.__name__}")


def _same(left: AdmissionValueT, right: AdmissionValueT, field: str, /) -> None:
    if left != right:
        raise ActContractError(f"{field} does not match")


def _stage_node_id(stage: ActHookStage, /) -> GraphNodeId:
    """Map an Act stage to the business node that produced its Hook value."""

    return GraphNodeId(str(ActNodeId(stage)))


@dataclass(frozen=True, slots=True)
class ActPayloadAdmission(Generic[HookStateT, HookCommandT]):
    """The single fixed outer admission contract used by Act nodes.

    Deep business-payload validation remains with the producer or Hook
    owner.  This class only checks exact outer classes, identity relationships,
    and the closed Act stage schema.
    """

    hook_state_type: type[HookStateT]
    hook_command_type: type[HookCommandT]

    def __post_init__(self) -> None:
        try:
            state_valid = self.hook_state_type is not HookStateProjection and issubclass(
                cast(type[object], self.hook_state_type),
                HookStateProjection,
            )
            command_valid = self.hook_command_type is not ActHookCommand and issubclass(
                cast(type[object], self.hook_command_type),
                ActHookCommand,
            )
        except TypeError as error:
            raise ActContractError("Act Hook state and command types must be concrete classes") from error
        if not state_valid:
            raise ActContractError("Act Hook state type must be a concrete HookStateProjection")
        if not command_valid:
            raise ActContractError("Act Hook command type must be a concrete ActHookCommand")

    def admit_act_slot(self, value: ActSlotId, /) -> ActSlotId:
        _exact(value, ActSlotId, "Act slot")
        return value

    def admit_graph_failure_reason(self, value: OpaqueGraphFailureReason, /) -> OpaqueGraphFailureReason:
        _exact(value, OpaqueGraphFailureReason, "Graph failure reason")
        return value

    def admit_request(self, value: ActRequest, /) -> ActRequest:
        _exact(value, ActRequest, "Act request")
        return value

    def admit_hook_state(self, value: HookStateProjection, /) -> HookStateT:
        if type(value) is not self.hook_state_type:
            raise ActContractError("Act Hook state has an unexpected concrete type")
        return cast(HookStateT, value)

    def admit_resolved_invocation(self, value: ResolvedInvocation, /) -> ResolvedInvocation:
        _exact(value, ResolvedInvocation, "resolved invocation")
        return value

    def admit_authorization_request_ref(self, value: AuthorizationRequestRef, /) -> AuthorizationRequestRef:
        _exact(value, AuthorizationRequestRef, "authorization request ref")
        return value

    def admit_interrupt_view(self, value: AuthorizationInterruptView, /) -> AuthorizationInterruptView:
        _exact(value, AuthorizationInterruptView, "authorization interrupt view")
        return value

    def admit_interrupt_payload(self, payload: bytes, /) -> bytes:
        return admit_authorization_interrupt_payload(payload)

    def admit_authorization_phase(self, value: AuthorizationPhase, /) -> AuthorizationPhase:
        if type(value) not in (InitialAuthorization, ResumedAuthorization):
            raise ActContractError("authorization phase must be initial or resumed")
        return value

    def admit_authorization_decision(self, value: AuthorizationDecision, /) -> AuthorizationDecision:
        if type(value) not in (Allow, Deny):
            raise ActContractError("authorization decision must be Allow or Deny")
        return value

    def admit_authorization_input(self, value: AuthorizationInput, /) -> AuthorizationInput:
        _exact(value, AuthorizationInput, "authorization input")
        self.admit_authorization_phase(value.phase)
        return value

    def admit_authorized_invocation(self, value: AuthorizedInvocation, /) -> AuthorizedInvocation:
        _exact(value, AuthorizedInvocation, "authorized invocation")
        return value

    def admit_resolution_result(self, value: ResolvePortResult, /) -> ResolvePortResult:
        if type(value) not in (ResolvedInvocation, ResolutionStopped):
            raise ActContractError("ResolvePort returned an unsupported result")
        return value

    def admit_execution_result(
        self,
        invocation: AuthorizedInvocation,
        value: ToolExecutionResult,
        /,
    ) -> ToolExecutionResult:
        _exact(invocation, AuthorizedInvocation, "execution invocation")
        _exact(value, ToolExecutionResult, "tool execution result")
        _same(value.identity.pairing, invocation.resolved.request.pairing, "execution pairing")
        _same(value.identity.binding, invocation.resolved.binding, "execution binding")
        _same(value.identity.arguments_digest, invocation.resolved.arguments.digest, "execution arguments digest")
        return value

    def admit_execution_outcome(self, value: ExecutePortResult, /) -> ExecutePortResult:
        if type(value) not in (ToolExecutionResult, ExecutionStopped):
            raise ActContractError("ExecutePort returned an unsupported result")
        return value

    def admit_settlement_projection(
        self,
        execution: ToolExecutionResult,
        value: SettlementProjection,
        /,
    ) -> SettlementProjection:
        _exact(execution, ToolExecutionResult, "settlement execution result")
        _exact(value, SettlementProjection, "settlement projection")
        _same(value.source_identity, execution.identity, "settlement source identity")
        return value

    def admit_write_request(self, value: ToolExchangeWriteRequest, /) -> ToolExchangeWriteRequest:
        _exact(value, ToolExchangeWriteRequest, "tool exchange write request")
        return value

    def admit_write_result(self, value: ToolExchangeWriteResult, /) -> ToolExchangeWriteResult:
        _exact(value, ToolExchangeWriteResult, "tool exchange write result")
        return value

    def admit_settled_result(self, value: SettledActResult, /) -> SettledActResult:
        _exact(value, SettledActResult, "settled Act result")
        return value

    def admit_hook_envelope(self, value: ActHookEnvelope, /) -> ActHookEnvelope:
        _exact(value, ActHookEnvelope, "Act Hook envelope")
        self._admit_stage_payload(value)
        return value

    def admit_hook_request(
        self,
        value: HookActivationRequest[ActHookEnvelope, HookStateT],
        /,
    ) -> HookActivationRequest[ActHookEnvelope, HookStateT]:
        _exact(value, HookActivationRequest, "Act Hook activation")
        self.admit_hook_envelope(value.value)
        if type(value.state) is not self.hook_state_type:
            raise ActContractError("Act Hook request state has an unexpected concrete type")
        if type(value.value.hook_state) is not self.hook_state_type:
            raise ActContractError("Act Hook envelope state has an unexpected concrete type")
        _same(value.state, value.value.hook_state, "Hook request state")
        expected_node = _stage_node_id(value.value.stage)
        if value.node_id is not None and value.node_id != expected_node:
            raise ActContractError("Act Hook request node_id does not match its stage")
        return value

    def admit_hook_result(
        self,
        value: HookGraphValue,
        /,
    ) -> HookResult[ActHookEnvelope, HookCommandT]:
        if type(value) is not HookResult:
            raise ActContractError("Act Hook result must be an exact HookResult")
        result = cast(HookResult[ActHookEnvelope, HookCommandT], value)
        self.admit_hook_envelope(result.value)
        if type(result.value.hook_state) is not self.hook_state_type:
            raise ActContractError("Act Hook result state has an unexpected concrete type")
        if type(result.commands) is not tuple:
            raise ActContractError("Act Hook commands must be a tuple")
        if any(type(command) is not self.hook_command_type for command in result.commands):
            raise ActContractError("Act Hook commands have an unexpected concrete type")
        expected_node = _stage_node_id(result.value.stage)
        if result.node_id is not None and result.node_id != expected_node:
            raise ActContractError("Act Hook result node_id does not match its stage")
        return result

    def admit_transition(
        self,
        request: HookActivationRequest[ActHookEnvelope, HookStateT],
        result: HookStageResult[ActHookEnvelope, HookCommandT],
        /,
    ) -> None:
        """Require an Act Hook priority to preserve its complete stage value."""

        self.admit_hook_request(request)
        _exact(result, HookStageResult, "Act Hook stage result")
        self.admit_hook_envelope(result.value)
        if type(result.value.hook_state) is not self.hook_state_type:
            raise ActContractError("Act Hook stage result state has an unexpected concrete type")
        if type(result.commands) is not tuple:
            raise ActContractError("Act Hook stage commands must be a tuple")
        if any(type(command) is not self.hook_command_type for command in result.commands):
            raise ActContractError("Act Hook stage commands have an unexpected concrete type")
        _same(result.value, request.value, "Act Hook stage value")

    def _admit_stage_payload(self, value: ActHookEnvelope, /) -> None:
        if value.stage is ActHookStage.RESOLVE:
            if type(value.payload) is not ResolveStageValue:
                raise ActContractError("Resolve Hook envelope has an invalid stage payload")
        elif value.stage is ActHookStage.AUTHORIZE:
            if type(value.payload) is not AuthorizeStageValue:
                raise ActContractError("Authorize Hook envelope has an invalid stage payload")
        elif value.stage is ActHookStage.EXECUTE:
            if type(value.payload) is not ExecuteStageValue:
                raise ActContractError("Execute Hook envelope has an invalid stage payload")
        elif value.stage is ActHookStage.SETTLE:
            if type(value.payload) is not SettleStageValue:
                raise ActContractError("Settle Hook envelope has an invalid stage payload")
        else:
            raise ActContractError("Act Hook envelope has an unknown stage")


__all__ = ["ActPayloadAdmission"]
