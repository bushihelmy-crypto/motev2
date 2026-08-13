"""Prepare, claim, rebuild, execute, and settle one frontier attempt."""

from typing import TypeVar

from mote_kernel.execution.claim import ExecutionClaimOwner, PreparedExecutionClaim
from mote_kernel.execution.engine.admission import admit_tasks
from mote_kernel.execution.engine.claim_stage import prepare_claim, require_claim_tasks
from mote_kernel.execution.engine.frontier import FrontierPreparation, prepare_frontier
from mote_kernel.execution.engine.resource_stage import (
    execute_resource_waves,
    initial_resource_snapshot,
    validated_resource_nodes,
)
from mote_kernel.execution.engine.resume_input import effective_node_input
from mote_kernel.execution.engine.scheduler import execute_tasks
from mote_kernel.execution.engine.settlement import settle_tasks
from mote_kernel.execution.engine.task import ExecutableTask
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import (
    AbortedGraph,
    AwaitingResume,
    CompletedGraph,
    ExecutableFrontier,
    ExecutedFrontierAttempt,
    PrepareDisposition,
    PreparedResourceAdmission,
    StartMissingChildren,
    WaitForActiveChildren,
    WaitingForChildren,
)
from mote_kernel.state.graph_state import (
    GraphRunStatus,
    UpdateGraphResources,
    failed_node_ids,
    interrupted_node_ids,
    pending_node_ids,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def _materialize(
    graph: CompiledGraph[InputT, OutputT],
    request: StepRequest[InputT, OutputT],
    frontier: FrontierPreparation[InputT, OutputT],
) -> tuple[ExecutableTask[InputT], ...]:
    effective_inputs = {
        task.node_id: effective_node_input(graph, request.state, task.node_id, request.node_input)
        for task in frontier.tasks
    }
    return tuple(
        ExecutableTask(
            task,
            effective_inputs[task.node_id],
        )
        for task, _definition in frontier.executable_definitions
    )


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
    if state.execution is not None:
        raise ResultCollectionError("active execution requires its original one-shot claim")
    if not pending_node_ids(state.frontier):
        return AwaitingResume(failed_node_ids(state.frontier), interrupted_node_ids(state.frontier))
    frontier = prepare_frontier(graph, request)
    if frontier.missing_children:
        return WaitingForChildren(StartMissingChildren(frontier.missing_children))
    if frontier.active_children:
        return WaitingForChildren(WaitForActiveChildren(frontier.active_children))
    _materialize(graph, request, frontier)
    resources = state.resources
    resource_tasks = tuple(task for task, definition in frontier.executable_definitions if definition.resources)
    if resource_tasks:
        current = resources or initial_resource_snapshot(graph)
        admission = admit_tasks(graph, resource_tasks, current)
        if admission.snapshot != current:
            return ExecutableFrontier(
                admission=PreparedResourceAdmission(
                    admission.admitted_node_ids,
                    admission.waiting_node_ids,
                    UpdateGraphResources(state.revision, admission.snapshot),
                )
            )
    validated_resource_nodes(graph, frontier.tasks, resources)
    return ExecutableFrontier(claim=prepare_claim(owner, state, request.request_attempt_id, frontier.tasks))


async def execute_claimed_frontier(
    graph: CompiledGraph[InputT, OutputT],
    request: StepRequest[InputT, OutputT],
    claim: PreparedExecutionClaim,
) -> ExecutedFrontierAttempt[OutputT]:
    frontier = prepare_frontier(graph, request)
    if frontier.missing_children or frontier.active_children:
        raise ResultCollectionError("claimed frontier cannot wait for children")
    require_claim_tasks(claim, frontier.tasks)
    executables = _materialize(graph, request, frontier)
    resources = request.state.resources
    resource_nodes = validated_resource_nodes(graph, frontier.tasks, resources)
    if resource_nodes:
        if resources is None:
            raise ResultCollectionError("resource execution requires committed admission")
        ordinary_results = await execute_resource_waves(graph, executables, resources, resource_nodes)
    else:
        ordinary_results = await execute_tasks(graph, executables)
    results = tuple(sorted((*ordinary_results, *frontier.nested_results), key=lambda result: result.task.sort_key))
    command = settle_tasks(graph, request.state, frontier.tasks, results)
    return ExecutedFrontierAttempt(results, command)


__all__ = ["execute_claimed_frontier", "prepare_superstep"]
