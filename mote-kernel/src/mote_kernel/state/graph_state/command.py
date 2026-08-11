"""Typed commands that change recoverable graph-run state."""

from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.parallel import ParallelSnapshot
from mote_kernel.state.graph_state.model import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphFailure,
    GraphInterruptIdentity,
    GraphInterruptPayload,
    GraphJoinProgress,
    GraphNodeId,
    GraphResolutionCodec,
    GraphRunId,
    GraphTaskId,
    ParentGraphTask,
)


@dataclass(frozen=True, slots=True)
class StartGraphRun:
    """Create a graph run at its initial committed frontier."""

    run_id: GraphRunId
    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    frontier: tuple[GraphNodeId, ...]
    parent: ParentGraphTask | None = None
    resolution_codec: GraphResolutionCodec | None = None


@dataclass(frozen=True, slots=True)
class AdvanceGraphRun:
    """Commit the next frontier after one superstep settles."""

    expected_superstep: int
    execution: GraphExecutionToken
    expected_interrupt_generation: int | None
    frontier: tuple[GraphNodeId, ...]
    join_progress: tuple[GraphJoinProgress, ...] = ()


@dataclass(frozen=True, slots=True)
class ClaimGraphExecution:
    """Atomically claim one task batch for one executor attempt."""

    expected_superstep: int
    expected_execution_sequence: int
    expected_parallel: ParallelSnapshot | None
    expected_interrupt_generation: int | None
    attempt_id: GraphExecutionAttemptId
    task_ids: tuple[GraphTaskId, ...]


@dataclass(frozen=True, slots=True)
class FenceGraphExecution:
    """Clear one exact lease only after its executor has been stopped and fenced."""

    expected_superstep: int
    execution: GraphExecutionToken


@dataclass(frozen=True, slots=True)
class RequestGraphRunInterrupt:
    """Suspend one quiescent run with a caller-assigned tree interrupt identity."""

    expected_superstep: int
    identity: GraphInterruptIdentity
    request_payload: GraphInterruptPayload


@dataclass(frozen=True, slots=True)
class ResolveGraphRunInterrupt:
    """Persist one exact interrupt resolution and resume its graph run."""

    expected_superstep: int
    identity: GraphInterruptIdentity
    resolution_payload: GraphInterruptPayload


@dataclass(frozen=True, slots=True)
class CompleteGraphRun:
    """Mark a running graph as successfully complete."""

    expected_superstep: int
    execution: GraphExecutionToken
    expected_interrupt_generation: int | None


@dataclass(frozen=True, slots=True)
class FailGraphExecution:
    """Fail a running graph from a settled, fenced execution attempt."""

    expected_superstep: int
    execution: GraphExecutionToken
    expected_interrupt_generation: int | None
    failure: GraphFailure


@dataclass(frozen=True, slots=True)
class AbortGraphRun:
    """Terminate a quiescent running or suspended graph without claiming execution."""

    expected_superstep: int
    expected_interrupt_generation: int | None
    failure: GraphFailure


@dataclass(frozen=True, slots=True)
class UpdateGraphParallel:
    """Commit resource admission before an executor attempt is claimed."""

    expected_superstep: int
    expected_parallel: ParallelSnapshot | None
    expected_interrupt_generation: int | None
    parallel: ParallelSnapshot


GraphRunCommand: TypeAlias = (
    StartGraphRun
    | ClaimGraphExecution
    | FenceGraphExecution
    | RequestGraphRunInterrupt
    | ResolveGraphRunInterrupt
    | AdvanceGraphRun
    | CompleteGraphRun
    | FailGraphExecution
    | AbortGraphRun
    | UpdateGraphParallel
)

__all__ = [
    "AbortGraphRun",
    "AdvanceGraphRun",
    "ClaimGraphExecution",
    "CompleteGraphRun",
    "FailGraphExecution",
    "FenceGraphExecution",
    "GraphRunCommand",
    "RequestGraphRunInterrupt",
    "ResolveGraphRunInterrupt",
    "StartGraphRun",
    "UpdateGraphParallel",
]
