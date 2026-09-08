"""Immutable Act / Tool Use values crossing the Graph boundary.

This module defines only provider-neutral outer structure.  The bytes carried
by the opaque wrappers are owned and interpreted by the corresponding
provider, protocol, or authorization owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from mote_kernel.act.identity import (
    ActHookStage,
    ActSlotId,
    ArgumentsDigest,
    CallerIdentityRef,
    OpaqueArguments,
    OpaqueAuthorizationHandle,
    OpaqueDefinitionReference,
    OpaqueExecutionOutcome,
    OpaqueGraphFailureReason,
    OpaqueProtocolPayload,
    OpaqueToolExchangeReceipt,
    ToolBindingRef,
    ToolCallId,
    ToolExchangeScopeId,
    ToolExecutionIdentity,
    ToolPairingIdentity,
    ToolSelector,
)
from mote_kernel.hooks.contract import HookGraphValue, HookResult

_INTERRUPT_PAYLOAD_MAX_BYTES = 65_536
_SCOPE_MAX_SEGMENTS = 16
_SCOPE_MAX_BYTES = 4_096
ContractValueT = TypeVar("ContractValueT")


class ActContractError(ValueError):
    """Raised when an Act value violates its outer contract."""


class ResolvePortResult(HookGraphValue):
    """Closed result family returned by ResolvePort."""

    __slots__ = ()


class ExecutePortResult(HookGraphValue):
    """Closed result family returned by ExecutePort."""

    __slots__ = ()


class HookStateProjection(HookGraphValue):
    """Nominal base for the owner-provided shared-Hook state class."""

    __slots__ = ()


class ActHookCommand(HookGraphValue):
    """Nominal base for the owner-provided shared-Hook command class."""

    __slots__ = ()


def _require_exact(value: ContractValueT, expected: type[ContractValueT], field: str, /) -> None:
    if type(value) is not expected:
        raise ActContractError(f"{field} must be an exact {expected.__name__}")


def _require_hook_state(value: HookGraphValue, field: str, /) -> None:
    if type(value) is HookStateProjection or not isinstance(value, HookStateProjection):
        raise ActContractError(f"{field} must be a concrete HookStateProjection")


def _require_stage_value(value: HookGraphValue, field: str, /) -> None:
    if not isinstance(value, ActStageValue) or type(value) is ActStageValue:
        raise ActContractError(f"{field} must be a concrete ActStageValue")


def _require_scope(scope: tuple[str, ...], /) -> None:
    if type(scope) is not tuple or len(scope) > _SCOPE_MAX_SEGMENTS:
        raise ActContractError("interrupt scope must contain zero to sixteen segments")
    total = 0
    for segment in scope:
        if type(segment) is not str or not segment or segment != segment.strip() or "\n" in segment or "\r" in segment:
            raise ActContractError("interrupt scope segments must be canonical strings")
        try:
            length = len(segment.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ActContractError("interrupt scope segments must be valid UTF-8") from error
        total += length
        if total > _SCOPE_MAX_BYTES:
            raise ActContractError("interrupt scope exceeds its 4096-byte limit")
        if length > 256:
            raise ActContractError("interrupt scope segment exceeds its 256-byte limit")


def admit_authorization_interrupt_payload(payload: bytes, /) -> bytes:
    if type(payload) is not bytes:
        raise ActContractError("authorization interrupt request_payload must be bytes")
    if len(payload) > _INTERRUPT_PAYLOAD_MAX_BYTES:
        raise ActContractError("authorization interrupt request_payload exceeds its 65536-byte limit")
    return payload


class AuthorizationPhase(HookGraphValue):
    """Closed nominal base for initial and resumed authorization phases."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class InitialAuthorization(AuthorizationPhase):
    resolved: ResolvedInvocation

    def __post_init__(self) -> None:
        _require_exact(self.resolved, ResolvedInvocation, "initial authorization resolved")


class AuthorizationDecision(HookGraphValue):
    """Closed nominal base for the two structural resume decisions."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Allow(AuthorizationDecision):
    """Permit the already-resolved invocation."""


@dataclass(frozen=True, slots=True)
class Deny(AuthorizationDecision):
    """Stop the already-resolved invocation."""


@dataclass(frozen=True, slots=True)
class AuthorizationRequestRef(HookGraphValue):
    pairing: ToolPairingIdentity
    handle: OpaqueAuthorizationHandle

    def __post_init__(self) -> None:
        _require_exact(self.pairing, ToolPairingIdentity, "authorization request pairing")
        _require_exact(self.handle, OpaqueAuthorizationHandle, "authorization request handle")


@dataclass(frozen=True, slots=True)
class ResumedAuthorization(AuthorizationPhase):
    resolved: ResolvedInvocation
    request_ref: AuthorizationRequestRef
    decision: AuthorizationDecision

    def __post_init__(self) -> None:
        _require_exact(self.resolved, ResolvedInvocation, "resumed authorization resolved")
        _require_exact(self.request_ref, AuthorizationRequestRef, "resumed authorization request_ref")
        if type(self.decision) not in (Allow, Deny):
            raise ActContractError("resumed authorization decision must be Allow or Deny")
        if self.request_ref.pairing != self.resolved.request.pairing:
            raise ActContractError("authorization request pairing does not match resolved invocation")


@dataclass(frozen=True, slots=True)
class AuthorizationInput(HookGraphValue):
    phase: AuthorizationPhase

    def __post_init__(self) -> None:
        if type(self.phase) not in (InitialAuthorization, ResumedAuthorization):
            raise ActContractError("authorization input phase must be initial or resumed")

    @classmethod
    def initial(cls, resolved: ResolvedInvocation, /) -> AuthorizationInput:
        return cls(InitialAuthorization(resolved))

    @classmethod
    def resumed(
        cls,
        resolved: ResolvedInvocation,
        request_ref: AuthorizationRequestRef,
        decision: AuthorizationDecision,
        /,
    ) -> AuthorizationInput:
        return cls(ResumedAuthorization(resolved, request_ref, decision))


@dataclass(frozen=True, slots=True)
class ActRequest(HookGraphValue):
    pairing: ToolPairingIdentity
    selector: ToolSelector
    arguments: OpaqueArguments
    caller: CallerIdentityRef
    hook_state: HookStateProjection

    def __post_init__(self) -> None:
        _require_exact(self.pairing, ToolPairingIdentity, "Act request pairing")
        _require_exact(self.selector, ToolSelector, "Act request selector")
        _require_exact(self.arguments, OpaqueArguments, "Act request arguments")
        _require_exact(self.caller, CallerIdentityRef, "Act request caller")
        _require_hook_state(self.hook_state, "Act request hook_state")


@dataclass(frozen=True, slots=True)
class CanonicalArguments(HookGraphValue):
    payload: OpaqueArguments
    digest: ArgumentsDigest

    def __post_init__(self) -> None:
        _require_exact(self.payload, OpaqueArguments, "canonical arguments payload")
        _require_exact(self.digest, ArgumentsDigest, "canonical arguments digest")


@dataclass(frozen=True, slots=True)
class ResolvedInvocation(ResolvePortResult):
    request: ActRequest
    definition: OpaqueDefinitionReference
    binding: ToolBindingRef
    arguments: CanonicalArguments

    def __post_init__(self) -> None:
        _require_exact(self.request, ActRequest, "resolved invocation request")
        _require_exact(self.definition, OpaqueDefinitionReference, "resolved invocation definition")
        _require_exact(self.binding, ToolBindingRef, "resolved invocation binding")
        _require_exact(self.arguments, CanonicalArguments, "resolved invocation arguments")


@dataclass(frozen=True, slots=True)
class ResolutionStopped(ResolvePortResult):
    reason: OpaqueGraphFailureReason

    def __post_init__(self) -> None:
        _require_exact(self.reason, OpaqueGraphFailureReason, "resolution stop reason")


@dataclass(frozen=True, slots=True)
class AuthorizedInvocation(HookGraphValue):
    resolved: ResolvedInvocation

    def __post_init__(self) -> None:
        _require_exact(self.resolved, ResolvedInvocation, "authorized invocation resolved")


@dataclass(frozen=True, slots=True)
class ExecutionStopped(ExecutePortResult):
    reason: OpaqueGraphFailureReason

    def __post_init__(self) -> None:
        _require_exact(self.reason, OpaqueGraphFailureReason, "execution stop reason")


@dataclass(frozen=True, slots=True)
class ToolExecutionResult(ExecutePortResult):
    identity: ToolExecutionIdentity
    outcome: OpaqueExecutionOutcome

    def __post_init__(self) -> None:
        _require_exact(self.identity, ToolExecutionIdentity, "tool execution result identity")
        _require_exact(self.outcome, OpaqueExecutionOutcome, "tool execution result outcome")


@dataclass(frozen=True, slots=True)
class SettlementProjection(HookGraphValue):
    source_identity: ToolExecutionIdentity
    payload: OpaqueProtocolPayload

    def __post_init__(self) -> None:
        _require_exact(self.source_identity, ToolExecutionIdentity, "settlement projection source_identity")
        _require_exact(self.payload, OpaqueProtocolPayload, "settlement projection payload")


@dataclass(frozen=True, slots=True)
class ToolExchangeWriteRequest(HookGraphValue):
    projection: SettlementProjection

    def __post_init__(self) -> None:
        _require_exact(self.projection, SettlementProjection, "tool exchange write projection")


@dataclass(frozen=True, slots=True)
class ToolExchangeWriteResult(HookGraphValue):
    receipt: OpaqueToolExchangeReceipt

    def __post_init__(self) -> None:
        _require_exact(self.receipt, OpaqueToolExchangeReceipt, "tool exchange write receipt")


@dataclass(frozen=True, slots=True)
class SettledActResult(HookGraphValue):
    projection: SettlementProjection
    receipt: OpaqueToolExchangeReceipt

    def __post_init__(self) -> None:
        _require_exact(self.projection, SettlementProjection, "settled Act projection")
        _require_exact(self.receipt, OpaqueToolExchangeReceipt, "settled Act receipt")


class ActStageValue(HookGraphValue):
    """Closed nominal base for the four shared-Hook payloads."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class ResolveStageValue(ActStageValue):
    authorization: AuthorizationInput

    def __post_init__(self) -> None:
        _require_exact(self.authorization, AuthorizationInput, "resolve stage authorization")


@dataclass(frozen=True, slots=True)
class AuthorizeStageValue(ActStageValue):
    invocation: AuthorizedInvocation

    def __post_init__(self) -> None:
        _require_exact(self.invocation, AuthorizedInvocation, "authorize stage invocation")


@dataclass(frozen=True, slots=True)
class ExecuteStageValue(ActStageValue):
    result: ToolExecutionResult

    def __post_init__(self) -> None:
        _require_exact(self.result, ToolExecutionResult, "execute stage result")


@dataclass(frozen=True, slots=True)
class SettleStageValue(ActStageValue):
    result: SettledActResult

    def __post_init__(self) -> None:
        _require_exact(self.result, SettledActResult, "settle stage result")


@dataclass(frozen=True, slots=True)
class ActHookEnvelope(HookGraphValue):
    """The current Act stage value entering or leaving the shared Hook.

    The envelope carries the current business stage, its typed result, and the
    shared Hook state.  The containing Graph routes the Hook's nested
    completion from the originating business-node identity; no next-hop value
    is carried in this payload.
    """

    stage: ActHookStage
    payload: ActStageValue
    hook_state: HookStateProjection

    def __post_init__(self) -> None:
        if type(self.stage) is not ActHookStage:
            raise ActContractError("Act Hook envelope stage must be an ActHookStage")
        _require_stage_value(self.payload, "Act Hook envelope payload")
        _require_hook_state(self.hook_state, "Act Hook envelope hook_state")


@dataclass(frozen=True, slots=True)
class AuthorizationInterruptView(HookGraphValue):
    scope: tuple[str, ...]
    node_id: str
    interrupt_id: str
    request_payload: bytes

    def __post_init__(self) -> None:
        _require_scope(self.scope)
        if type(self.node_id) is not str or self.node_id != "authorize":
            raise ActContractError("authorization interrupt node_id must be authorize")
        if (
            type(self.interrupt_id) is not str
            or not self.interrupt_id
            or self.interrupt_id != self.interrupt_id.strip()
            or "\n" in self.interrupt_id
            or "\r" in self.interrupt_id
        ):
            raise ActContractError("authorization interrupt_id must be canonical")
        try:
            interrupt_length = len(self.interrupt_id.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ActContractError("authorization interrupt_id must be valid UTF-8") from error
        if interrupt_length > 256:
            raise ActContractError("authorization interrupt_id exceeds its 256-byte limit")
        admit_authorization_interrupt_payload(self.request_payload)


__all__ = [
    "ActContractError",
    "ActHookCommand",
    "ActHookEnvelope",
    "ActHookStage",
    "ActRequest",
    "ActSlotId",
    "ActStageValue",
    "Allow",
    "ArgumentsDigest",
    "AuthorizationDecision",
    "AuthorizationInput",
    "AuthorizationInterruptView",
    "AuthorizationPhase",
    "AuthorizationRequestRef",
    "AuthorizeStageValue",
    "AuthorizedInvocation",
    "CallerIdentityRef",
    "CanonicalArguments",
    "Deny",
    "ExecutePortResult",
    "ExecuteStageValue",
    "ExecutionStopped",
    "HookGraphValue",
    "HookResult",
    "HookStateProjection",
    "InitialAuthorization",
    "OpaqueArguments",
    "OpaqueAuthorizationHandle",
    "OpaqueDefinitionReference",
    "OpaqueExecutionOutcome",
    "OpaqueGraphFailureReason",
    "OpaqueProtocolPayload",
    "OpaqueToolExchangeReceipt",
    "ResolutionStopped",
    "ResolvePortResult",
    "ResolveStageValue",
    "ResolvedInvocation",
    "ResumedAuthorization",
    "SettleStageValue",
    "SettledActResult",
    "SettlementProjection",
    "ToolBindingRef",
    "ToolCallId",
    "ToolExchangeScopeId",
    "ToolExchangeWriteRequest",
    "ToolExchangeWriteResult",
    "ToolExecutionIdentity",
    "ToolExecutionResult",
    "ToolPairingIdentity",
    "ToolSelector",
]
