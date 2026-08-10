"""Authoritative, recoverable graph-run state."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import NewType

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


__all__ = [
    "GraphDefinitionId",
    "GraphDefinitionVersion",
    "GraphFailure",
    "GraphNodeId",
    "GraphRunId",
    "GraphRunState",
    "GraphRunStatus",
    "GraphTaskId",
    "ParentGraphTask",
]
