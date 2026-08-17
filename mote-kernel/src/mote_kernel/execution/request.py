"""Typed requests for scoped graph execution and selective recovery."""

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from mote_kernel.execution.graph.values import _GraphValues
from mote_kernel.execution.identity import ExecutionRequestAttemptId, ScopeRunCoordinate
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import ChildProjection
from mote_kernel.execution.run_context import ScopedFrameIndex
from mote_kernel.state.graph_state import GraphInterruptId, GraphNodeId, GraphRunState

GraphValueT = TypeVar("GraphValueT")


@dataclass(frozen=True, slots=True)
class StepRequest(Generic[GraphValueT]):
    state: GraphRunState
    scope_run: ScopeRunCoordinate
    frames: ScopedFrameIndex[GraphValueT]
    request_attempt_id: ExecutionRequestAttemptId
    child_projections: tuple[ChildProjection[GraphValueT], ...]
    limits: ExecutionLimits


@dataclass(frozen=True, slots=True)
class UseMaterializedInput:
    pass


@dataclass(frozen=True, slots=True)
class OverrideNodeInput(Generic[GraphValueT]):
    values: _GraphValues[GraphValueT]


@dataclass(frozen=True, slots=True)
class ResumeFailedNodeRequest(Generic[GraphValueT]):
    scope: tuple[GraphNodeId, ...]
    node_id: GraphNodeId
    input: UseMaterializedInput | OverrideNodeInput[GraphValueT]


@dataclass(frozen=True, slots=True)
class ResumeInterruptedNodeRequest(Generic[GraphValueT]):
    scope: tuple[GraphNodeId, ...]
    node_id: GraphNodeId
    interrupt_id: GraphInterruptId
    input: OverrideNodeInput[GraphValueT]


@dataclass(frozen=True, slots=True)
class SkipFailedNodeRequest:
    scope: tuple[GraphNodeId, ...]
    node_id: GraphNodeId
    reason: str
    route: str | None


ResumeNodeRequest: TypeAlias = (
    ResumeFailedNodeRequest[GraphValueT] | ResumeInterruptedNodeRequest[GraphValueT] | SkipFailedNodeRequest
)


@dataclass(frozen=True, slots=True)
class ResumeRequest(Generic[GraphValueT]):
    state: GraphRunState
    scope_run: ScopeRunCoordinate
    frames: ScopedFrameIndex[GraphValueT]
    actions: tuple[ResumeNodeRequest[GraphValueT], ...]


__all__: list[str] = []
