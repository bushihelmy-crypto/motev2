"""Typed requests for stepping a graph from committed state."""

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import NestedTaskResult, TaskResult
from mote_kernel.state.graph_state import GraphRunState

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class StepRequest(Generic[InputT, OutputT]):
    """Execute one superstep with one shared immutable node input snapshot."""

    graph: CompiledGraph[InputT, OutputT]
    state: GraphRunState
    node_input: InputT
    limits: ExecutionLimits = field(default_factory=ExecutionLimits)
    nested_results: tuple[NestedTaskResult[OutputT], ...] = ()
    settled_results: tuple[TaskResult[OutputT], ...] = ()


__all__ = ["StepRequest"]
