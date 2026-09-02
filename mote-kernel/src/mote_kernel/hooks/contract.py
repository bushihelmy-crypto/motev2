"""Typed values and configuration contracts consumed by HookNode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, runtime_checkable

from mote_kernel.execution.errors import GraphValidationError
from mote_kernel.execution.graph.ports import canonical_nominal_type
from mote_kernel.hooks.plan import HookConfigSnapshot, HookPlan, HookPriorityPlan

ConfigT = TypeVar("ConfigT")
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


def _validate_nominal_type(payload_type: type[PayloadT], field: str, /) -> None:
    try:
        canonical_nominal_type(payload_type)
    except GraphValidationError as error:
        raise HookContractError(f"hook {field} type must be one concrete nominal class") from error


def _admit_exact(payload: PayloadT, expected: type[PayloadT], field: str, /) -> PayloadT:
    if type(payload) is not expected:
        raise HookContractError(f"hook {field} has an unexpected payload type")
    return payload


@dataclass(frozen=True, slots=True)
class HookRequest(HookGraphValue, Generic[ValueT, StateT]):
    """The current value and owner-provided read-only state for one priority.

    The owner supplies an immutable state value or read-only view; freezing this
    envelope does not deep-freeze the payload.
    """

    value: ValueT
    state: StateT


@dataclass(frozen=True, slots=True)
class HookPayloadAdmission(Generic[ConfigT, PriorityConfigT, ValueT, StateT, CommandT]):
    """The one nominal runtime contract for a concrete Hook payload family.

    Python type parameters disappear at runtime.  The composition root therefore
    supplies the concrete classes once, and every Hook boundary reuses this
    immutable admission contract instead of guessing from annotations.
    """

    config_type: type[ConfigT]
    priority_config_type: type[PriorityConfigT]
    value_type: type[ValueT]
    state_type: type[StateT]
    command_type: type[CommandT]

    def __post_init__(self) -> None:
        _validate_nominal_type(self.config_type, "config")
        _validate_nominal_type(self.priority_config_type, "priority config")
        _validate_nominal_type(self.value_type, "value")
        _validate_nominal_type(self.state_type, "state")
        _validate_nominal_type(self.command_type, "command")

    def admit_snapshot(
        self,
        snapshot: HookConfigSnapshot[ConfigT],
        /,
    ) -> HookConfigSnapshot[ConfigT]:
        if type(snapshot) is not HookConfigSnapshot:
            raise HookContractError("hook config source must return a HookConfigSnapshot")
        _admit_exact(snapshot.config, self.config_type, "config")
        return snapshot

    def admit_plan(self, plan: HookPlan[PriorityConfigT], /) -> HookPlan[PriorityConfigT]:
        if type(plan) is not HookPlan:
            raise HookContractError("hook plan loader must return a HookPlan")
        for priority_name, priority_plan in (
            ("P1", plan.p1),
            ("P2", plan.p2),
            ("P3", plan.p3),
        ):
            if type(priority_plan) is not HookPriorityPlan:
                raise HookContractError(f"hook plan {priority_name} must be a HookPriorityPlan")
            _admit_exact(priority_plan.config, self.priority_config_type, f"{priority_name} config")
        return plan

    def admit_request(self, request: HookRequest[ValueT, StateT], /) -> HookRequest[ValueT, StateT]:
        if type(request) is not HookRequest:
            raise HookContractError("hook invocation must contain a HookRequest")
        _admit_exact(request.value, self.value_type, "value")
        _admit_exact(request.state, self.state_type, "state")
        return request

    def admit_invocation_request(
        self,
        request: HookInvocationRequest[PriorityConfigT, ValueT, StateT],
        /,
    ) -> HookInvocationRequest[PriorityConfigT, ValueT, StateT]:
        if type(request) is not HookInvocationRequest:
            raise HookContractError("hook invocation request must be a HookInvocationRequest")
        _admit_exact(request.config, self.priority_config_type, "priority config")
        self.admit_request(request.request)
        return request

    def admit_stage_result(
        self,
        result: HookStageResult[ValueT, CommandT],
        /,
    ) -> HookStageResult[ValueT, CommandT]:
        if type(result) is not HookStageResult:
            raise HookContractError("hook invocation must return a HookStageResult")
        _admit_exact(result.value, self.value_type, "value")
        if type(result.commands) is not tuple:
            raise HookContractError("hook stage result commands must be a tuple")
        for command in result.commands:
            _admit_exact(command, self.command_type, "command")
        return result

    def admit_result(self, result: HookResult[ValueT, CommandT], /) -> HookResult[ValueT, CommandT]:
        if type(result) is not HookResult:
            raise HookContractError("hook result must be a HookResult")
        _admit_exact(result.value, self.value_type, "value")
        if type(result.commands) is not tuple:
            raise HookContractError("hook result commands must be a tuple")
        for command in result.commands:
            _admit_exact(command, self.command_type, "command")
        return result


@dataclass(frozen=True, slots=True)
class HookInvocationRequest(HookGraphValue, Generic[PriorityConfigT, ValueT, StateT]):
    """Hook-owned typed envelope passed through the shared Invocation.

    A concrete invocation adapter admits the config and payload types before
    constructing the stage result.
    """

    config: PriorityConfigT
    request: HookRequest[ValueT, StateT]

    def __post_init__(self) -> None:
        if type(self.request) is not HookRequest:
            raise TypeError("hook invocation request must contain a HookRequest")


@dataclass(frozen=True, slots=True)
class HookStageResult(HookGraphValue, Generic[ValueT, CommandT]):
    """One priority's value and invocation-owned ordered command delta.

    Command meaning and payload admission remain with the concrete owner.
    """

    value: ValueT
    commands: tuple[CommandT, ...] = ()

    def __post_init__(self) -> None:
        if type(self.commands) is not tuple:
            raise TypeError("hook stage result commands must be a tuple")


@dataclass(frozen=True, slots=True)
class HookResult(HookGraphValue, Generic[ValueT, CommandT]):
    """The HookNode's final value and P1-to-P3 ordered command delta."""

    value: ValueT
    commands: tuple[CommandT, ...] = ()

    def __post_init__(self) -> None:
        if type(self.commands) is not tuple:
            raise TypeError("hook result commands must be a tuple")


@runtime_checkable
class HookConfigSource(Protocol[ConfigT]):
    """Read the current immutable configuration once for a HookNode invocation."""

    def snapshot(self) -> HookConfigSnapshot[ConfigT]: ...


@runtime_checkable
class HookPlanLoader(Protocol[ConfigT, PriorityConfigT]):
    """Build one immutable dynamic plan from the captured configuration."""

    def load(self, snapshot: HookConfigSnapshot[ConfigT], /) -> HookPlan[PriorityConfigT]: ...


__all__ = [
    "HookConfigSource",
    "HookContractError",
    "HookGraphValue",
    "HookInvocationRequest",
    "HookPayloadAdmission",
    "HookPlanLoader",
    "HookRequest",
    "HookResult",
    "HookStageResult",
]
