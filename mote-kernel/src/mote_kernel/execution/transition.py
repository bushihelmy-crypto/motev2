"""Execution outcomes awaiting authoritative state transition."""

from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.execution.graph.identity import NodeId
from mote_kernel.execution.snapshot import ExecutionToken, JoinProgress


@dataclass(frozen=True, slots=True)
class AdvanceTransition:
    """Advance one settled superstep to the next committed frontier."""

    expected_revision: int
    execution: ExecutionToken
    frontier: tuple[NodeId, ...]
    join_progress: tuple[JoinProgress, ...] = ()


@dataclass(frozen=True, slots=True)
class CompleteTransition:
    """Complete a settled graph with no remaining work."""

    expected_revision: int
    execution: ExecutionToken


@dataclass(frozen=True, slots=True)
class FailTransition:
    """Fail the superstep that produced the associated task failure."""

    expected_revision: int
    execution: ExecutionToken
    failure: str


ExecutionTransition: TypeAlias = AdvanceTransition | CompleteTransition | FailTransition

__all__ = ["AdvanceTransition", "CompleteTransition", "ExecutionTransition", "FailTransition"]
