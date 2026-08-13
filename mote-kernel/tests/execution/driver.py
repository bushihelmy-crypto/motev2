from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution import (
    ExecutableFrontier,
    ExecutedFrontierAttempt,
    ExecutionRequestAttemptId,
    GraphExecutor,
    StepRequest,
)
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import ChildProjection, PrepareDisposition
from mote_kernel.state.graph_state import GraphRunCommand, GraphRunState, reduce_graph_run

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
ATTEMPT_ID = ExecutionRequestAttemptId("test-request")
DEFAULT_LIMITS = ExecutionLimits()


@dataclass(frozen=True, slots=True)
class DriverRequest(Generic[InputT, OutputT]):
    graph: CompiledGraph[InputT, OutputT]
    state: GraphRunState
    node_input: InputT
    child_projections: tuple[ChildProjection[OutputT], ...]
    limits: ExecutionLimits

    def execution_request(self) -> StepRequest[InputT, OutputT]:
        return StepRequest(
            self.state,
            self.node_input,
            ATTEMPT_ID,
            self.child_projections,
            self.limits,
        )


@dataclass(frozen=True, slots=True)
class ClaimedStep(Generic[OutputT]):
    state: GraphRunState
    result: ExecutedFrontierAttempt[OutputT]


def step_request(
    graph: CompiledGraph[InputT, OutputT],
    state: GraphRunState,
    node_input: InputT,
    child_projections: tuple[ChildProjection[OutputT], ...] = (),
    limits: ExecutionLimits = DEFAULT_LIMITS,
) -> DriverRequest[InputT, OutputT]:
    return DriverRequest(graph, state, node_input, child_projections, limits)


def apply_command(state: GraphRunState, command: GraphRunCommand) -> GraphRunState:
    return reduce_graph_run(state, command)


async def execute_step(
    request: DriverRequest[InputT, OutputT],
) -> PrepareDisposition[InputT, OutputT] | ClaimedStep[OutputT]:
    executor = GraphExecutor(request.graph)
    prepared = await executor.prepare(request.execution_request())
    if not isinstance(prepared, ExecutableFrontier) or prepared.claim is None:
        return prepared
    claimed = apply_command(request.state, prepared.claim.command)
    result = await executor.execute(
        prepared.claim,
        StepRequest(
            claimed,
            request.node_input,
            ATTEMPT_ID,
            request.child_projections,
            request.limits,
        ),
    )
    return ClaimedStep(claimed, result)


def apply_claimed(step: ClaimedStep[OutputT]) -> GraphRunState:
    return apply_command(step.state, step.result.command)


__all__ = [
    "ATTEMPT_ID",
    "ClaimedStep",
    "DriverRequest",
    "apply_claimed",
    "apply_command",
    "execute_step",
    "step_request",
]
