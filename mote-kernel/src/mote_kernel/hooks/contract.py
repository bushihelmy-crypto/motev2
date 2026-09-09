"""Typed values and configuration contracts consumed by HookNode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, runtime_checkable

from mote_kernel.execution.errors import GraphValidationError
from mote_kernel.execution.graph.ports import canonical_nominal_type
from mote_kernel.hooks.plan import HookPlan, HookPriorityPlan
from mote_kernel.state.graph_state import GraphConfigCursor, GraphNodeId
from mote_kernel.state.graph_state.identity import is_canonical_identity

PriorityConfigT = TypeVar("PriorityConfigT")
ValueT = TypeVar("ValueT")
StateT = TypeVar("StateT")
CommandT = TypeVar("CommandT")
PayloadT = TypeVar("PayloadT")


class HookContractError(ValueError):
    """Raised when a hook value crosses its typed owner boundary incorrectly."""


class HookGraphValue:
    """Internal nominal base for values carried by the HookNode graph."""

    __slots__ = ()


@runtime_checkable
class HookTransitionAdmission(Protocol[ValueT, StateT, CommandT]):
    """Admit one concrete Hook priority transition for its composition owner."""

    def admit_transition(
        self,
        request: HookActivationRequest[ValueT, StateT],
        result: HookStageResult[ValueT, CommandT],
        /,
    ) -> None: ...


def _validate_nominal_type(payload_type: type[PayloadT], field: str, /) -> None:
    try:
        canonical_nominal_type(payload_type)
    except GraphValidationError as error:
        raise HookContractError(f"hook {field} type must be one concrete nominal class") from error


def _admit_exact(payload: PayloadT, expected: type[PayloadT], field: str, /) -> PayloadT:
    if type(payload) is not expected:
        raise HookContractError(f"hook {field} has an unexpected payload type")
    return payload


def _require_tuple(value: tuple[CommandT, ...], field: str, error_type: type[Exception], /) -> None:
    """Keep tuple-shape admission in the Hook contract owner."""

    if type(value) is not tuple:
        raise error_type(f"{field} must be a tuple")


def _admit_node_id(node_id: GraphNodeId | None, field: str, /) -> None:
    if node_id is not None and not is_canonical_identity(node_id):
        raise HookContractError(f"{field} must be a canonical GraphNodeId or None")


@dataclass(frozen=True, slots=True)
class HookActivationRequest(HookGraphValue, Generic[ValueT, StateT]):
    """Kernel-only business envelope entering the shared Hook graph.

    The complete Config lives beside this value in execution activation
    metadata.  Only the business value, read-only state and parent-owned node
    identity are present here.
    """

    value: ValueT
    state: StateT
    node_id: GraphNodeId | None = None

    def __post_init__(self) -> None:
        _admit_node_id(self.node_id, "hook activation request node_id")


@dataclass(frozen=True, slots=True)
class HookPayloadAdmission(Generic[PriorityConfigT, ValueT, StateT, CommandT]):
    """The one nominal runtime contract for a concrete Hook payload family.

    Python type parameters disappear at runtime.  The composition root therefore
    supplies the concrete classes once, and every Hook boundary reuses this
    immutable admission contract instead of guessing from annotations.
    """

    priority_config_type: type[PriorityConfigT]
    value_type: type[ValueT]
    state_type: type[StateT]
    command_type: type[CommandT]
    transition_admission: HookTransitionAdmission[ValueT, StateT, CommandT] | None = None

    def __post_init__(self) -> None:
        _validate_nominal_type(self.priority_config_type, "priority config")
        _validate_nominal_type(self.value_type, "value")
        _validate_nominal_type(self.state_type, "state")
        _validate_nominal_type(self.command_type, "command")
        transition_admission = self.transition_admission
        if transition_admission is not None:
            try:
                admit_transition = transition_admission.admit_transition
            except AttributeError as error:
                raise HookContractError("hook transition admission must satisfy HookTransitionAdmission") from error
            if not callable(admit_transition):
                raise HookContractError("hook transition admission must satisfy HookTransitionAdmission")

    def admit_plan(self, plan: HookPlan[PriorityConfigT], /) -> HookPlan[PriorityConfigT]:
        if type(plan) is not HookPlan:
            raise HookContractError("hook node requires a HookPlan")
        for priority_name, priority_plan in (
            ("P1", plan.p1),
            ("P2", plan.p2),
        ):
            if type(priority_plan) is not HookPriorityPlan:
                raise HookContractError(f"hook plan {priority_name} must be a HookPriorityPlan")
            _admit_exact(priority_plan.config, self.priority_config_type, f"{priority_name} config")
        return plan

    def admit_request(
        self,
        request: HookActivationRequest[ValueT, StateT],
        /,
    ) -> HookActivationRequest[ValueT, StateT]:
        if type(request) is not HookActivationRequest:
            raise HookContractError("hook activation must contain a HookActivationRequest")
        _admit_exact(request.value, self.value_type, "value")
        _admit_exact(request.state, self.state_type, "state")
        _admit_node_id(request.node_id, "hook activation request node_id")
        return request

    def admit_invocation_request(
        self,
        request: HookInvocationRequest[PriorityConfigT, ValueT],
        /,
    ) -> HookInvocationRequest[PriorityConfigT, ValueT]:
        if type(request) is not HookInvocationRequest:
            raise HookContractError("hook invocation boundary requires a HookInvocationRequest")
        _admit_exact(request.hook_config, self.priority_config_type, "hook priority config")
        _admit_exact(request.payload, self.value_type, "hook payload")
        if request.config_cursor is not None and type(request.config_cursor) is not GraphConfigCursor:
            raise HookContractError("hook invocation config_cursor must be a GraphConfigCursor or None")
        return request

    def admit_stage_result(
        self,
        result: HookStageResult[ValueT, CommandT],
        /,
    ) -> HookStageResult[ValueT, CommandT]:
        if type(result) is not HookStageResult:
            raise HookContractError("hook invocation must return a HookStageResult")
        _admit_exact(result.value, self.value_type, "value")
        _require_tuple(result.commands, "hook stage result commands", HookContractError)
        for command in result.commands:
            _admit_exact(command, self.command_type, "command")
        return result

    def admit_transition(
        self,
        request: HookActivationRequest[ValueT, StateT],
        result: HookStageResult[ValueT, CommandT],
        /,
    ) -> None:
        admission = self.transition_admission
        if admission is not None:
            admission.admit_transition(request, result)

    def admit_result(self, result: HookResult[ValueT, CommandT], /) -> HookResult[ValueT, CommandT]:
        if type(result) is not HookResult:
            raise HookContractError("hook result must be a HookResult")
        _admit_exact(result.value, self.value_type, "value")
        _require_tuple(result.commands, "hook result commands", HookContractError)
        for command in result.commands:
            _admit_exact(command, self.command_type, "command")
        _admit_node_id(result.node_id, "hook result node_id")
        return result


@dataclass(frozen=True, slots=True)
class HookInvocationRequest(HookGraphValue, Generic[PriorityConfigT, ValueT]):
    """The narrow DTO crossing the Hook ``Invocation`` boundary.

    ``hook_config`` is the current P1 or P2 priority projection.  ``payload``
    is the business value only; the Kernel activation envelope, state and
    routing identity are intentionally absent.  ``config_cursor`` lets a
    local or remote implementation correlate the call with the immutable
    snapshot without granting it access to the complete Config.
    """

    hook_config: PriorityConfigT
    payload: ValueT
    config_cursor: GraphConfigCursor | None = None

    def __post_init__(self) -> None:
        if self.config_cursor is not None and type(self.config_cursor) is not GraphConfigCursor:
            raise TypeError("hook invocation config_cursor must be a GraphConfigCursor or None")


@dataclass(frozen=True, slots=True)
class HookStageResult(HookGraphValue, Generic[ValueT, CommandT]):
    """One priority's value and invocation-owned ordered command delta.

    Command meaning and payload admission remain with the concrete owner.
    """

    value: ValueT
    commands: tuple[CommandT, ...] = ()

    def __post_init__(self) -> None:
        _require_tuple(self.commands, "hook stage result commands", TypeError)


@dataclass(frozen=True, slots=True)
class HookResult(HookGraphValue, Generic[ValueT, CommandT]):
    """The HookNode's final value and P1-to-P2 ordered command delta."""

    value: ValueT
    commands: tuple[CommandT, ...] = ()
    node_id: GraphNodeId | None = None

    def __post_init__(self) -> None:
        _require_tuple(self.commands, "hook result commands", TypeError)
        _admit_node_id(self.node_id, "hook result node_id")


__all__ = [
    "HookContractError",
    "HookGraphValue",
    "HookInvocationRequest",
    "HookPayloadAdmission",
    "HookResult",
    "HookStageResult",
    "HookTransitionAdmission",
]
