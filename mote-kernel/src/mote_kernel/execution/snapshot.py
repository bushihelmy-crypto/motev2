"""Read-only execution projection supplied by the state owner."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import NewType

from mote_kernel.execution.graph import GraphDefinitionId, GraphDefinitionVersion, NodeId

GraphRunId = NewType("GraphRunId", str)
ParentTaskId = NewType("ParentTaskId", str)


class ExecutionStatus(Enum):
    """Execution-facing projection of a graph run lifecycle."""

    RUNNING = auto()
    SUSPENDED = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class ParentTaskRef:
    """Stable parent linkage projected for nested graph execution."""

    run_id: GraphRunId
    task_id: ParentTaskId


@dataclass(frozen=True, slots=True)
class JoinProgress:
    """Recoverable arrivals for one static join that has not fired yet."""

    sources: tuple[NodeId, ...]
    target: NodeId
    arrived: frozenset[NodeId]


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    """Immutable committed facts consumed by pure execution algorithms."""

    run_id: GraphRunId
    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    status: ExecutionStatus
    superstep: int
    frontier: tuple[NodeId, ...]
    parent: ParentTaskRef | None = None
    join_progress: tuple[JoinProgress, ...] = ()


__all__ = ["ExecutionSnapshot", "ExecutionStatus", "GraphRunId", "JoinProgress", "ParentTaskId", "ParentTaskRef"]
