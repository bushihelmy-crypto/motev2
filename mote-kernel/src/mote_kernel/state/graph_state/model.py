"""Authoritative, recoverable graph-run state."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import NewType

from mote_kernel.parallel import ParallelSnapshot

GraphRunId = NewType("GraphRunId", str)
GraphDefinitionId = NewType("GraphDefinitionId", str)
GraphDefinitionVersion = NewType("GraphDefinitionVersion", int)
GraphNodeId = NewType("GraphNodeId", str)
GraphFailure = NewType("GraphFailure", str)
GraphTaskId = NewType("GraphTaskId", str)


class GraphRunStatus(Enum):
    """Durable lifecycle state of one graph run."""

    RUNNING = auto()
    SUSPENDED = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class ParentGraphTask:
    """Stable parent linkage for a graph invoked as a node."""

    run_id: GraphRunId
    task_id: GraphTaskId


@dataclass(frozen=True, slots=True)
class GraphJoinProgress:
    """Durable arrivals for one static join that has not fired yet."""

    sources: tuple[GraphNodeId, ...]
    target: GraphNodeId
    arrived: frozenset[GraphNodeId]


@dataclass(frozen=True, slots=True)
class GraphRunState:
    """The committed execution position for one graph run."""

    run_id: GraphRunId
    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    status: GraphRunStatus
    superstep: int
    frontier: tuple[GraphNodeId, ...]
    parent: ParentGraphTask | None = None
    failure: GraphFailure | None = None
    join_progress: tuple[GraphJoinProgress, ...] = ()
    parallel: ParallelSnapshot | None = None
    settled_tasks: tuple[GraphTaskId, ...] = ()


__all__ = [
    "GraphDefinitionId",
    "GraphDefinitionVersion",
    "GraphFailure",
    "GraphJoinProgress",
    "GraphNodeId",
    "GraphRunId",
    "GraphRunState",
    "GraphRunStatus",
    "GraphTaskId",
    "ParentGraphTask",
]
