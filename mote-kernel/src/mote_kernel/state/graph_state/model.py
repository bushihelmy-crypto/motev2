"""Authoritative, recoverable graph-run state."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import NewType

from mote_kernel.state.graph_state.resource_model import ResourceSnapshot

GraphRunId = NewType("GraphRunId", str)
GraphDefinitionId = NewType("GraphDefinitionId", str)
GraphDefinitionVersion = NewType("GraphDefinitionVersion", int)
GraphNodeId = NewType("GraphNodeId", str)
GraphFailure = NewType("GraphFailure", str)
GraphTaskId = NewType("GraphTaskId", str)
GraphExecutionAttemptId = NewType("GraphExecutionAttemptId", str)
GraphInterruptId = NewType("GraphInterruptId", str)
GraphInterruptPayload = NewType("GraphInterruptPayload", bytes)
GraphResolutionCodecId = NewType("GraphResolutionCodecId", str)


class GraphRunStatus(Enum):
    """Durable lifecycle state of one graph run."""

    RUNNING = auto()
    SUSPENDED = auto()
    COMPLETED = auto()
    FAILED = auto()


class GraphInterruptLifecycle(Enum):
    """Durable lifecycle of one graph-tree interrupt generation."""

    REQUESTED = auto()
    RESOLVED = auto()
    CONSUMED = auto()
    CANCELLED = auto()


@dataclass(frozen=True, slots=True)
class GraphExecutionToken:
    """Fence one executor attempt from every earlier or later attempt."""

    generation: int
    attempt_id: GraphExecutionAttemptId


@dataclass(frozen=True, slots=True)
class GraphExecutionLease:
    """Durable ownership of one exact task batch by one executor attempt."""

    token: GraphExecutionToken
    task_ids: tuple[GraphTaskId, ...]


@dataclass(frozen=True, slots=True)
class GraphResolutionCodec:
    """Durable codec identity fixed by one versioned graph definition."""

    codec_id: GraphResolutionCodecId
    version: int


@dataclass(frozen=True, slots=True)
class GraphInterruptIdentity:
    """One monotonic interrupt generation for an authoritative graph tree."""

    root_run_id: GraphRunId
    interrupt_id: GraphInterruptId
    generation: int


@dataclass(frozen=True, slots=True)
class GraphInterruptReceipt:
    """Proof that graph progress consumed a resolution or termination finalized an interrupt."""

    superstep: int


@dataclass(frozen=True, slots=True)
class GraphInterruptRecord:
    """Durable request, resolution codec, payload, and one-shot lifecycle."""

    identity: GraphInterruptIdentity
    request_payload: GraphInterruptPayload
    resolution_codec: GraphResolutionCodec
    lifecycle: GraphInterruptLifecycle
    resolution_payload: GraphInterruptPayload | None = None
    receipt: GraphInterruptReceipt | None = None


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
    resources: ResourceSnapshot | None = None
    execution_sequence: int = 0
    execution: GraphExecutionLease | None = None
    interrupt: GraphInterruptRecord | None = None
    resolution_codec: GraphResolutionCodec | None = None


__all__ = [
    "GraphDefinitionId",
    "GraphDefinitionVersion",
    "GraphExecutionAttemptId",
    "GraphExecutionLease",
    "GraphExecutionToken",
    "GraphFailure",
    "GraphInterruptId",
    "GraphInterruptIdentity",
    "GraphInterruptLifecycle",
    "GraphInterruptPayload",
    "GraphInterruptReceipt",
    "GraphInterruptRecord",
    "GraphJoinProgress",
    "GraphNodeId",
    "GraphResolutionCodec",
    "GraphResolutionCodecId",
    "GraphRunId",
    "GraphRunState",
    "GraphRunStatus",
    "GraphTaskId",
    "ParentGraphTask",
]
