"""Read-only execution projection supplied by the state owner."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import NewType

from mote_kernel.execution.graph import GraphDefinitionId, GraphDefinitionVersion, NodeId
from mote_kernel.state.graph_state.resource_model import ResourceSnapshot

GraphRunId = NewType("GraphRunId", str)
ParentTaskId = NewType("ParentTaskId", str)
ExecutionAttemptId = NewType("ExecutionAttemptId", str)
ExecutionTaskId = NewType("ExecutionTaskId", str)
InterruptId = NewType("InterruptId", str)
InterruptPayload = NewType("InterruptPayload", bytes)
ResolutionCodecId = NewType("ResolutionCodecId", str)


class ExecutionStatus(Enum):
    """Execution-facing projection of a graph run lifecycle."""

    RUNNING = auto()
    SUSPENDED = auto()
    COMPLETED = auto()
    FAILED = auto()


class InterruptLifecycle(Enum):
    """Execution projection of one durable interrupt generation."""

    REQUESTED = auto()
    RESOLVED = auto()
    CONSUMED = auto()
    CANCELLED = auto()


@dataclass(frozen=True, slots=True)
class ExecutionToken:
    """Executor-owned fencing token for one committed task batch."""

    generation: int
    attempt_id: ExecutionAttemptId


@dataclass(frozen=True, slots=True)
class ExecutionLeaseSnapshot:
    """Read-only projection of the current durable execution owner."""

    token: ExecutionToken
    task_ids: tuple[ExecutionTaskId, ...]


@dataclass(frozen=True, slots=True)
class InterruptReceipt:
    """Execution projection of the superstep that finalized a resolution."""

    superstep: int


@dataclass(frozen=True, slots=True)
class InterruptRecord:
    """Read-only durable interrupt projection consumed by execution."""

    root_run_id: GraphRunId
    interrupt_id: InterruptId
    generation: int
    request_payload: InterruptPayload
    resolution_codec_id: ResolutionCodecId
    resolution_codec_version: int
    lifecycle: InterruptLifecycle
    resolution_payload: InterruptPayload | None = None
    receipt: InterruptReceipt | None = None


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
    resources: ResourceSnapshot | None = None
    execution_sequence: int = 0
    execution: ExecutionLeaseSnapshot | None = None
    interrupt: InterruptRecord | None = None
    revision: int = 0


__all__ = [
    "ExecutionAttemptId",
    "ExecutionLeaseSnapshot",
    "ExecutionSnapshot",
    "ExecutionStatus",
    "ExecutionTaskId",
    "ExecutionToken",
    "GraphRunId",
    "InterruptId",
    "InterruptLifecycle",
    "InterruptPayload",
    "InterruptReceipt",
    "InterruptRecord",
    "JoinProgress",
    "ParentTaskId",
    "ParentTaskRef",
    "ResolutionCodecId",
]
