"""State-driven preparation and session creation for one frontier attempt."""

from typing import TypeVar

from mote_kernel.execution.claim import ExecutionClaimOwner, PreparedExecutionClaim
from mote_kernel.execution.engine.admission import admit_tasks, initial_resource_snapshot
from mote_kernel.execution.engine.claim_stage import prepare_claim, require_claim_tasks
from mote_kernel.execution.engine.frontier import FrontierPreparation, prepare_frontier
from mote_kernel.execution.engine.resume_input import effective_node_input
from mote_kernel.execution.engine.routing import resolve_routing
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import (
    AbortedGraph,
    AwaitingResume,
    CompletedGraph,
    ExecutableFrontier,
    PrepareDisposition,
    ReadyToResolve,
    StartMissingChildren,
    WaitForActiveChildren,
    WaitingForChildren,
)
from mote_kernel.state.graph_state import (
    GraphFrontierStatus,
    GraphRunStatus,
    ResourceSnapshot,
    failed_node_ids,
    frontier_status,
    interrupted_node_ids,
    routing_contributions,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def _validate_inputs(
    graph: CompiledGraph[InputT, OutputT],
    request: StepRequest[InputT, OutputT],
    frontier: FrontierPreparation[InputT, OutputT],
) -> None:
    for task, _definition in frontier.executable_definitions:
        effective_node_input(graph, request.state, task.node_id, request.node_input)


def _claim_resources(
    graph: CompiledGraph[InputT, OutputT],
    frontier: FrontierPreparation[InputT, OutputT],
) -> ResourceSnapshot | None:
    resource_tasks = tuple(task for task, definition in frontier.executable_definitions if definition.resources)
    if not resource_tasks:
        return None
    admission = admit_tasks(graph, resource_tasks, initial_resource_snapshot(graph))
    if not admission.snapshot.acquisitions:
        raise ResultCollectionError("resource admission did not create acquisition participants")
    return admission.snapshot


async def prepare_superstep(
    owner: ExecutionClaimOwner,
    graph: CompiledGraph[InputT, OutputT],
    request: StepRequest[InputT, OutputT],
) -> PrepareDisposition[InputT, OutputT]:
    state = request.state
    if state.status is GraphRunStatus.COMPLETED:
        return CompletedGraph()
    if state.status is GraphRunStatus.ABORTED:
        return AbortedGraph()
    status = frontier_status(state.frontier)
    if status is GraphFrontierStatus.SETTLED:
        return ReadyToResolve(
            resolve_routing(
                graph,
                routing_contributions(state.frontier),
                state.join_progress,
                expected_revision=state.revision,
            )
        )
    if status is GraphFrontierStatus.AWAITING_RESUME:
        return AwaitingResume(failed_node_ids(state.frontier), interrupted_node_ids(state.frontier))
    if state.execution is not None:
        raise ResultCollectionError("active execution requires its original execution session")
    frontier = prepare_frontier(graph, request)
    if frontier.missing_children:
        return WaitingForChildren(StartMissingChildren(frontier.missing_children))
    if frontier.active_children:
        return WaitingForChildren(WaitForActiveChildren(frontier.active_children))
    _validate_inputs(graph, request, frontier)
    claim = prepare_claim(
        owner,
        state,
        request.request_attempt_id,
        frontier.tasks,
        _claim_resources(graph, frontier),
    )
    return ExecutableFrontier(claim)


def validate_execution_session_request(
    graph: CompiledGraph[InputT, OutputT],
    request: StepRequest[InputT, OutputT],
    claim: PreparedExecutionClaim,
) -> None:
    frontier = prepare_frontier(graph, request)
    if frontier.missing_children or frontier.active_children:
        raise ResultCollectionError("claimed frontier cannot wait for children")
    require_claim_tasks(claim, frontier.tasks)


__all__ = ["prepare_superstep", "validate_execution_session_request"]
