"""Authoritative, recoverable graph-run state."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import NewType

from mote_kernel.state.graph_state.frontier_model import GraphFrontierState, GraphResumeInputCodec
from mote_kernel.state.graph_state.identity import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphNodeId,
    GraphRunId,
)
from mote_kernel.state.graph_state.resource_model import ResourceSnapshot

GraphAbortReason = NewType("GraphAbortReason", str)


class GraphRunStatus(Enum):
    RUNNING = auto()
    COMPLETED = auto()
    ABORTED = auto()


@dataclass(frozen=True, slots=True)
class GraphAbort:
    reason: GraphAbortReason


@dataclass(frozen=True, slots=True)
class GraphExecutionToken:
    generation: int
    attempt_id: GraphExecutionAttemptId


@dataclass(frozen=True, slots=True)
class GraphExecutionLease:
    token: GraphExecutionToken


@dataclass(frozen=True, slots=True)
class ParentGraphActivation:
    run_id: GraphRunId
    superstep: int
    node_id: GraphNodeId


@dataclass(frozen=True, slots=True)
class GraphJoinProgress:
    sources: tuple[GraphNodeId, ...]
    target: GraphNodeId
    arrived: frozenset[GraphNodeId]


@dataclass(frozen=True, slots=True)
class GraphRunState:
    run_id: GraphRunId
    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    status: GraphRunStatus
    superstep: int
    frontier: GraphFrontierState
    execution_sequence: int = 0
    resume_input_codec: GraphResumeInputCodec | None = None
    join_progress: tuple[GraphJoinProgress, ...] = ()
    resources: ResourceSnapshot | None = None
    execution: GraphExecutionLease | None = None
    abort: GraphAbort | None = None
    parent: ParentGraphActivation | None = None
    revision: int = 0


__all__ = [
    "GraphAbort",
    "GraphAbortReason",
    "GraphExecutionLease",
    "GraphExecutionToken",
    "GraphJoinProgress",
    "GraphRunState",
    "GraphRunStatus",
    "ParentGraphActivation",
]
