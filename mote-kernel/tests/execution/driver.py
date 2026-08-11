from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution import (
    ExecutedSuperstep,
    GraphExecutor,
    StepRequest,
)
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import NestedTaskResult, StepResult
from mote_kernel.execution.snapshot import ExecutionAttemptId
from mote_kernel.state.graph_state import GraphRunCommand, GraphRunState
from mote_kernel.state.graph_state.reducer import reduce_graph_run

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
ATTEMPT_ID = ExecutionAttemptId("test-attempt")
DEFAULT_LIMITS = ExecutionLimits()


@dataclass(frozen=True, slots=True)
class DriverRequest(Generic[InputT, OutputT]):
    graph: CompiledGraph[InputT, OutputT]
    state: GraphRunState
    node_input: InputT
    limits: ExecutionLimits
    nested_results: tuple[NestedTaskResult[OutputT], ...]

    def execution_request(self) -> StepRequest[InputT, OutputT]:
        return StepRequest(
            self.state,
            self.node_input,
            ATTEMPT_ID,
            self.limits,
            self.nested_results,
        )


@dataclass(frozen=True, slots=True)
class ClaimedStep(Generic[OutputT]):
    """An execution result paired with the exact reducer-produced claim state."""

    state: GraphRunState
    result: ExecutedSuperstep[OutputT]


def step_request(
    graph: CompiledGraph[InputT, OutputT],
    state: GraphRunState,
    node_input: InputT,
    limits: ExecutionLimits = DEFAULT_LIMITS,
    nested_results: tuple[NestedTaskResult[OutputT], ...] = (),
) -> DriverRequest[InputT, OutputT]:
    return DriverRequest(graph, state, node_input, limits, nested_results)


def reduce_graph_command(state: GraphRunState, command: GraphRunCommand) -> GraphRunState:
    """Apply the pure graph-state reducer without asserting a persistence topology."""

    return reduce_graph_run(state, command)


async def execute_step(request: DriverRequest[InputT, OutputT]) -> StepResult[InputT, OutputT]:
    """Prepare, reduce, and consume one exact execution claim."""

    executor = GraphExecutor(request.graph)
    execution_request = request.execution_request()
    prepared = await executor.prepare(execution_request)
    if prepared.execution is None or prepared.admission is not None:
        return prepared
    claimed = reduce_graph_command(request.state, prepared.execution.command)
    return await executor.execute(
        prepared.execution,
        StepRequest(
            claimed,
            request.node_input,
            ATTEMPT_ID,
            request.limits,
            request.nested_results,
        ),
    )


async def execute_claim(request: DriverRequest[InputT, OutputT]) -> ClaimedStep[OutputT]:
    """Reduce one prepared claim before consuming its linear capability."""

    executor = GraphExecutor(request.graph)
    prepared = await executor.prepare(request.execution_request())
    assert prepared.admission is None
    assert prepared.execution is not None
    claimed = reduce_graph_command(request.state, prepared.execution.command)
    result = await executor.execute(
        prepared.execution,
        StepRequest(
            claimed,
            request.node_input,
            ATTEMPT_ID,
            request.limits,
            request.nested_results,
        ),
    )
    return ClaimedStep(claimed, result)


def reduce_claim_result(step: ClaimedStep[OutputT]) -> GraphRunState:
    """Reduce an execution result against its exact claimed state."""

    return reduce_graph_command(step.state, step.result.command)


__all__ = [
    "ClaimedStep",
    "DriverRequest",
    "execute_claim",
    "execute_step",
    "reduce_claim_result",
    "reduce_graph_command",
    "step_request",
]
