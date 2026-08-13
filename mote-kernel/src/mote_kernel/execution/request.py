"""Typed requests for graph execution and selective node recovery."""

from dataclasses import dataclass, field
from typing import Generic, TypeAlias, TypeVar

from mote_kernel.execution.identity import ExecutionRequestAttemptId
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import ChildProjection
from mote_kernel.state.graph_state import (
    GraphInterruptId,
    GraphNodeId,
    GraphRoutingContribution,
    GraphRunState,
    GraphSkipReason,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class StepRequest(Generic[InputT, OutputT]):
    state: GraphRunState
    node_input: InputT
    request_attempt_id: ExecutionRequestAttemptId
    child_projections: tuple[ChildProjection[OutputT], ...]
    limits: ExecutionLimits = field(default_factory=ExecutionLimits)


@dataclass(frozen=True, slots=True)
class UseRequestInput:
    pass


@dataclass(frozen=True, slots=True)
class OverrideNodeInput(Generic[InputT]):
    value: InputT


@dataclass(frozen=True, slots=True)
class ResumeFailedNodeRequest(Generic[InputT]):
    node_id: GraphNodeId
    input: UseRequestInput | OverrideNodeInput[InputT]


@dataclass(frozen=True, slots=True)
class ResumeInterruptedNodeRequest(Generic[InputT]):
    node_id: GraphNodeId
    interrupt_id: GraphInterruptId
    input: OverrideNodeInput[InputT]


@dataclass(frozen=True, slots=True)
class SkipFailedNodeRequest:
    node_id: GraphNodeId
    reason: GraphSkipReason
    routing: GraphRoutingContribution


ResumeNodeRequest: TypeAlias = (
    ResumeFailedNodeRequest[InputT] | ResumeInterruptedNodeRequest[InputT] | SkipFailedNodeRequest
)


@dataclass(frozen=True, slots=True)
class ResumeRequest(Generic[InputT]):
    state: GraphRunState
    actions: tuple[ResumeNodeRequest[InputT], ...]


__all__ = [
    "ExecutionRequestAttemptId",
    "OverrideNodeInput",
    "ResumeFailedNodeRequest",
    "ResumeInterruptedNodeRequest",
    "ResumeNodeRequest",
    "ResumeRequest",
    "SkipFailedNodeRequest",
    "StepRequest",
    "UseRequestInput",
]
