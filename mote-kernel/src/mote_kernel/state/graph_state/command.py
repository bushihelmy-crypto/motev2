"""Typed commands that change recoverable graph-run state."""

from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.state.graph_state.model import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphFailure,
    GraphJoinProgress,
    GraphNodeId,
    GraphRunId,
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


@dataclass(frozen=True, slots=True)
class AdvanceGraphRun:
    """Commit the next frontier after one superstep settles."""

    expected_superstep: int
    frontier: tuple[GraphNodeId, ...]
    join_progress: tuple[GraphJoinProgress, ...] = ()


@dataclass(frozen=True, slots=True)
class SuspendGraphRun:
    """Pause a running graph without losing its frontier."""


@dataclass(frozen=True, slots=True)
class ResumeGraphRun:
    """Resume a suspended graph at its committed frontier."""


@dataclass(frozen=True, slots=True)
class CompleteGraphRun:
    """Mark a running graph as successfully complete."""

    expected_superstep: int


@dataclass(frozen=True, slots=True)
class FailGraphRun:
    """Mark a running or suspended graph as failed."""

    expected_superstep: int
    failure: GraphFailure


GraphRunCommand: TypeAlias = (
    StartGraphRun | AdvanceGraphRun | SuspendGraphRun | ResumeGraphRun | CompleteGraphRun | FailGraphRun
)

__all__ = [
    "AdvanceGraphRun",
    "CompleteGraphRun",
    "FailGraphRun",
    "GraphRunCommand",
    "ResumeGraphRun",
    "StartGraphRun",
    "SuspendGraphRun",
]
