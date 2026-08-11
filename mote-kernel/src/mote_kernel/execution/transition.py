"""Execution outcomes awaiting authoritative state transition."""

from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.execution.graph.identity import NodeId
from mote_kernel.execution.snapshot import ExecutionToken, JoinProgress


@dataclass(frozen=True, slots=True)
class AdvanceTransition:
    """Advance one settled superstep to the next committed frontier."""

    expected_superstep: int
    execution: ExecutionToken
    expected_interrupt_generation: int | None
    frontier: tuple[NodeId, ...]
    join_progress: tuple[JoinProgress, ...] = ()


@dataclass(frozen=True, slots=True)
class CompleteTransition:
    """Complete a settled graph with no remaining work."""

    expected_superstep: int
    execution: ExecutionToken
    expected_interrupt_generation: int | None


@dataclass(frozen=True, slots=True)
class FailTransition:
    """Fail the superstep that produced the associated task failure."""

    expected_superstep: int
    execution: ExecutionToken
    expected_interrupt_generation: int | None
    failure: str


ExecutionTransition: TypeAlias = AdvanceTransition | CompleteTransition | FailTransition

__all__ = ["AdvanceTransition", "CompleteTransition", "ExecutionTransition", "FailTransition"]
