"""Typed execution results and state-driven preparation dispositions."""

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from mote_kernel.execution.claim import PreparedExecutionClaim
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.state.graph_state import (
    AdvanceGraphFrontier,
    CompleteGraphFrontier,
    GraphFailure,
    GraphInterruptPayload,
    GraphNodeId,
    GraphRoutingContribution,
    GraphRunState,
    ParentGraphActivation,
    SettleGraphNode,
    StartGraphRun,
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
class ExecutableFrontier:
    claim: PreparedExecutionClaim


@dataclass(frozen=True, slots=True)
class ReadyToResolve:
    command: AdvanceGraphFrontier | CompleteGraphFrontier


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
    ExecutableFrontier
    | WaitingForChildren[InputT, OutputT]
    | ReadyToResolve
    | AwaitingResume
    | CompletedGraph
    | AbortedGraph
)


@dataclass(frozen=True, slots=True)
class ExecutedGraphNode(Generic[OutputT]):
    result: TaskResult[OutputT]
    command: SettleGraphNode


__all__ = [
    "AbortedChild",
    "AbortedGraph",
    "ActiveChild",
    "AwaitingResume",
    "ChildProjection",
    "CompletedChild",
    "CompletedGraph",
    "ExecutableFrontier",
    "ExecutedGraphNode",
    "MissingChild",
    "PrepareDisposition",
    "PreparedNestedRun",
    "ReadyToResolve",
    "StartMissingChildren",
    "TaskFailure",
    "TaskInterrupt",
    "TaskResult",
    "TaskSuccess",
    "WaitForActiveChildren",
    "WaitingForChildren",
]
