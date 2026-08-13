"""Typed execution results and preparation dispositions."""

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from mote_kernel.execution.claim import PreparedExecutionClaim
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.state.graph_state import (
    GraphFailure,
    GraphInterruptPayload,
    GraphNodeId,
    GraphRoutingContribution,
    GraphRunCommand,
    GraphRunState,
    ParentGraphActivation,
    StartGraphRun,
    UpdateGraphResources,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class TaskSuccess(Generic[OutputT]):
    task: GraphTask
    output: OutputT
    routing: GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class TaskFailure:
    task: GraphTask
    failure: GraphFailure


@dataclass(frozen=True, slots=True)
class TaskInterrupt:
    task: GraphTask
    request_payload: GraphInterruptPayload


TaskResult: TypeAlias = TaskSuccess[OutputT] | TaskFailure | TaskInterrupt


@dataclass(frozen=True, slots=True)
class MissingChild:
    parent: ParentGraphActivation


@dataclass(frozen=True, slots=True)
class ActiveChild:
    parent: ParentGraphActivation
    child_state: GraphRunState


@dataclass(frozen=True, slots=True)
class CompletedChild(Generic[OutputT]):
    parent: ParentGraphActivation
    child_state: GraphRunState
    output: OutputT
    routing: GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class AbortedChild:
    parent: ParentGraphActivation
    child_state: GraphRunState


ChildProjection: TypeAlias = MissingChild | ActiveChild | CompletedChild[OutputT] | AbortedChild


@dataclass(frozen=True, slots=True)
class PreparedNestedRun(Generic[InputT, OutputT]):
    parent: ParentGraphActivation
    graph: CompiledGraph[InputT, OutputT]
    command: StartGraphRun


@dataclass(frozen=True, slots=True)
class StartMissingChildren(Generic[InputT, OutputT]):
    children: tuple[PreparedNestedRun[InputT, OutputT], ...]

    def __post_init__(self) -> None:
        parents = tuple(child.parent for child in self.children)
        if (
            not parents
            or len(parents) != len(set(parents))
            or parents != tuple(sorted(parents, key=lambda parent: (parent.run_id, parent.superstep, parent.node_id)))
        ):
            raise ValueError("children to start must be non-empty and canonical")


@dataclass(frozen=True, slots=True)
class WaitForActiveChildren:
    children: tuple[ActiveChild, ...]

    def __post_init__(self) -> None:
        parents = tuple(child.parent for child in self.children)
        if (
            not parents
            or len(parents) != len(set(parents))
            or parents != tuple(sorted(parents, key=lambda parent: (parent.run_id, parent.superstep, parent.node_id)))
        ):
            raise ValueError("active children must be non-empty and canonical")


ChildWaitAction: TypeAlias = StartMissingChildren[InputT, OutputT] | WaitForActiveChildren


@dataclass(frozen=True, slots=True)
class WaitingForChildren(Generic[InputT, OutputT]):
    action: ChildWaitAction[InputT, OutputT]


@dataclass(frozen=True, slots=True)
class PreparedResourceAdmission:
    admitted_node_ids: tuple[GraphNodeId, ...]
    waiting_node_ids: tuple[GraphNodeId, ...]
    command: UpdateGraphResources


@dataclass(frozen=True, slots=True)
class ExecutableFrontier:
    admission: PreparedResourceAdmission | None = None
    claim: PreparedExecutionClaim | None = None

    def __post_init__(self) -> None:
        if (self.admission is None) == (self.claim is None):
            raise ValueError("an executable frontier requires exactly one admission or claim")


@dataclass(frozen=True, slots=True)
class AwaitingResume:
    failed_node_ids: tuple[GraphNodeId, ...]
    interrupted_node_ids: tuple[GraphNodeId, ...]


@dataclass(frozen=True, slots=True)
class CompletedGraph:
    pass


@dataclass(frozen=True, slots=True)
class AbortedGraph:
    pass


PrepareDisposition: TypeAlias = (
    ExecutableFrontier | WaitingForChildren[InputT, OutputT] | AwaitingResume | CompletedGraph | AbortedGraph
)


@dataclass(frozen=True, slots=True)
class ExecutedFrontierAttempt(Generic[OutputT]):
    results: tuple[TaskResult[OutputT], ...]
    command: GraphRunCommand


__all__ = [
    "AbortedChild",
    "AbortedGraph",
    "ActiveChild",
    "AwaitingResume",
    "ChildProjection",
    "CompletedChild",
    "CompletedGraph",
    "ExecutableFrontier",
    "ExecutedFrontierAttempt",
    "MissingChild",
    "PrepareDisposition",
    "PreparedNestedRun",
    "PreparedResourceAdmission",
    "StartMissingChildren",
    "TaskFailure",
    "TaskInterrupt",
    "TaskResult",
    "TaskSuccess",
    "WaitForActiveChildren",
    "WaitingForChildren",
]
