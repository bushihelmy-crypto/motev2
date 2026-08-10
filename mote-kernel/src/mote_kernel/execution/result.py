"""Typed results of one graph task invocation."""

from dataclasses import dataclass, field
from typing import Generic, TypeAlias, TypeVar

from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.graph.command import Continue, RoutingCommand
from mote_kernel.state.graph_state import GraphRunCommand, GraphRunState, StartGraphRun, UpdateGraphParallel

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class TaskSuccess(Generic[OutputT]):
    """A task completed once and emitted a typed routing command."""

    task: GraphTask
    output: OutputT
    routing: RoutingCommand = field(default_factory=Continue)


@dataclass(frozen=True, slots=True)
class TaskFailure:
    """A task invocation settled as a graph-level failure."""

    task: GraphTask
    failure: str


TaskResult: TypeAlias = TaskSuccess[OutputT] | TaskFailure


@dataclass(frozen=True, slots=True)
class ExecutedSuperstep(Generic[OutputT]):
    """Stable node results and the command proposed for durable state."""

    results: tuple[TaskResult[OutputT], ...]
    command: GraphRunCommand


@dataclass(frozen=True, slots=True)
class PreparedResourceAdmission:
    """The resource reservation part of a prepared frontier."""

    admitted: tuple[GraphTask, ...]
    waiting: tuple[GraphTask, ...]
    command: UpdateGraphParallel


@dataclass(frozen=True, slots=True)
class ExecutedFrontierBatch(Generic[OutputT]):
    """A partial frontier result batch and its state command awaiting commit."""

    results: tuple[TaskResult[OutputT], ...]
    command: UpdateGraphParallel


@dataclass(frozen=True, slots=True)
class NestedTaskSuccess(Generic[OutputT]):
    """A child success loaded atomically with its committed terminal state."""

    task_id: TaskId
    child_state: GraphRunState
    output: OutputT
    routing: RoutingCommand = field(default_factory=Continue)


@dataclass(frozen=True, slots=True)
class NestedTaskFailure:
    """A child failure loaded atomically with its committed terminal state."""

    task_id: TaskId
    child_state: GraphRunState
    failure: str


NestedTaskResult: TypeAlias = NestedTaskSuccess[OutputT] | NestedTaskFailure


@dataclass(frozen=True, slots=True)
class PreparedNestedRun(Generic[InputT, OutputT]):
    """One child graph run that must be committed before it can execute."""

    parent_task: GraphTask
    graph: CompiledGraph[InputT, OutputT]
    command: StartGraphRun


@dataclass(frozen=True, slots=True)
class PreparedFrontier(Generic[InputT, OutputT]):
    """All state and child-run preparation required before a frontier can execute."""

    admission: PreparedResourceAdmission | None
    nested_runs: tuple[PreparedNestedRun[InputT, OutputT], ...]


StepResult: TypeAlias = PreparedFrontier[InputT, OutputT] | ExecutedFrontierBatch[OutputT] | ExecutedSuperstep[OutputT]

__all__ = [
    "ExecutedFrontierBatch",
    "ExecutedSuperstep",
    "NestedTaskFailure",
    "NestedTaskResult",
    "NestedTaskSuccess",
    "PreparedFrontier",
    "PreparedNestedRun",
    "PreparedResourceAdmission",
    "StepResult",
    "TaskFailure",
    "TaskResult",
    "TaskSuccess",
]
