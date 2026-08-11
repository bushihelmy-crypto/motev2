"""Typed requests for stepping a graph from committed state."""

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import NestedTaskResult
from mote_kernel.execution.snapshot import ExecutionAttemptId
from mote_kernel.state.graph_state import GraphRunState

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class StepRequest(Generic[InputT, OutputT]):
    """Prepare or execute one state-selected graph with one shared immutable input."""

    state: GraphRunState
    node_input: InputT
    attempt_id: ExecutionAttemptId
    limits: ExecutionLimits = field(default_factory=ExecutionLimits)
    nested_results: tuple[NestedTaskResult[OutputT], ...] = ()


__all__ = ["StepRequest"]
