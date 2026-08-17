from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.admission import admit_graph_input
from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.identity import ExecutionRequestAttemptId, root_scope_run
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import (
    ChildProjection,
    ExecutableFrontier,
    ExecutedGraphNode,
    PrepareDisposition,
)
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    GraphInputAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import GraphRunCommand, GraphRunState, reduce_graph_run

GraphValueT = TypeVar("GraphValueT")
ATTEMPT_ID = ExecutionRequestAttemptId("test-request")
DEFAULT_LIMITS = ExecutionLimits()


@dataclass(frozen=True, slots=True)
class DriverRequest(Generic[GraphValueT]):
    graph: CompiledGraph[GraphValueT]
    state: GraphRunState
    values: Graph.Values[GraphValueT]
    child_projections: tuple[ChildProjection[GraphValueT], ...]
    limits: ExecutionLimits

    def execution_request(self) -> StepRequest[GraphValueT]:
        scope_run = root_scope_run(self.state.run_id)
        input_frame = admit_graph_input(self.graph, self.values)
        frames: ScopedFrameIndex[GraphValueT] = ScopedFrameIndex()
        frames = frames.add_graph_input(
            AdmittedGraphInput(
                GraphInputAvailabilityCoordinate(scope_run, self.graph.graph_input_descriptor.identity),
                input_frame,
            )
        )
        return StepRequest(
            self.state,
            scope_run,
            frames,
            ATTEMPT_ID,
            self.child_projections,
            self.limits,
        )


@dataclass(slots=True)
class ClaimedStep(Generic[GraphValueT]):
    state: GraphRunState
    session: GraphExecutionSession[GraphValueT]
    result: ExecutedGraphNode[GraphValueT]


def step_request(
    graph: CompiledGraph[str],
    state: GraphRunState,
    node_input: str,
    child_projections: tuple[ChildProjection[str], ...] = (),
    limits: ExecutionLimits = DEFAULT_LIMITS,
) -> DriverRequest[str]:
    return DriverRequest(graph, state, Graph.values(value=node_input), child_projections, limits)


def apply_command(state: GraphRunState, command: GraphRunCommand) -> GraphRunState:
    return reduce_graph_run(state, command)


async def execute_step(
    request: DriverRequest[GraphValueT],
) -> PrepareDisposition[GraphValueT] | ClaimedStep[GraphValueT]:
    executor = GraphExecutor(request.graph)
    execution_request = request.execution_request()
    prepared = await executor.prepare(execution_request)
    if not isinstance(prepared, ExecutableFrontier):
        return prepared
    claimed = apply_command(request.state, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        StepRequest(
            claimed,
            execution_request.scope_run,
            execution_request.frames,
            ATTEMPT_ID,
            request.child_projections,
            request.limits,
        ),
    )
    result = await session.next(claimed)
    return ClaimedStep(claimed, session, result)


def apply_claimed(step: ClaimedStep[GraphValueT]) -> GraphRunState:
    return apply_command(step.state, step.result.command)


async def close_claimed(step: ClaimedStep[GraphValueT]) -> None:
    await step.session.aclose()


__all__ = [
    "ATTEMPT_ID",
    "ClaimedStep",
    "DriverRequest",
    "apply_claimed",
    "apply_command",
    "close_claimed",
    "execute_step",
    "step_request",
]
