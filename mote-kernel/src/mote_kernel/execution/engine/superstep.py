"""Explicit prepare, claim, execute, and settle orchestration for one superstep."""

from typing import TypeVar

from mote_kernel.execution.claim import ExecutionClaimOwner, PreparedExecutionClaim
from mote_kernel.execution.engine.admission import admit_tasks
from mote_kernel.execution.engine.claim_stage import interrupt_generation, prepare_claim, require_claim_tasks
from mote_kernel.execution.engine.frontier import prepare_frontier
from mote_kernel.execution.engine.resolution_input import effective_node_input
from mote_kernel.execution.engine.resource_stage import (
    execute_resource_waves,
    initial_parallel_snapshot,
    validated_resource_tasks,
)
from mote_kernel.execution.engine.scheduler import execute_tasks
from mote_kernel.execution.engine.settlement import settle_tasks
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.graph_run import project_graph_command
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import (
    ExecutedSuperstep,
    PreparedFrontier,
    PreparedResourceAdmission,
)
from mote_kernel.state.graph_state import UpdateGraphParallel

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


async def prepare_superstep(
    owner: ExecutionClaimOwner,
    graph: CompiledGraph[InputT, OutputT],
    request: StepRequest[InputT, OutputT],
) -> PreparedFrontier[InputT, OutputT]:
    """Prepare nested runs, resource admission, or one linear execution claim."""

    frontier = prepare_frontier(graph, request)
    parallel = request.state.parallel
    resource_tasks = validated_resource_tasks(graph, frontier, parallel)
    if not frontier.pending_tasks:
        return PreparedFrontier(None, ())
    if frontier.nested_runs:
        return PreparedFrontier(None, frontier.nested_runs)
    if resource_tasks:
        current = parallel or initial_parallel_snapshot(graph)
        admission = admit_tasks(
            graph,
            tuple(task for task, _definition in frontier.executable_definitions),
            current,
        )
        if admission.snapshot != current:
            return PreparedFrontier(
                PreparedResourceAdmission(
                    admission.admitted,
                    admission.waiting,
                    UpdateGraphParallel(
                        request.state.superstep,
                        parallel,
                        interrupt_generation(request.state),
                        admission.snapshot,
                    ),
                ),
                frontier.nested_runs,
            )
        return PreparedFrontier(
            None,
            (),
            prepare_claim(owner, request.state, request.attempt_id, frontier.pending_tasks),
        )
    return PreparedFrontier(
        None,
        (),
        prepare_claim(owner, request.state, request.attempt_id, frontier.pending_tasks),
    )


async def execute_claimed_superstep(
    graph: CompiledGraph[InputT, OutputT],
    request: StepRequest[InputT, OutputT],
    claim: PreparedExecutionClaim,
) -> ExecutedSuperstep[OutputT]:
    """Invoke and settle the exact recomputed batch of one consumed accepted claim."""

    frontier = prepare_frontier(graph, request)
    parallel = request.state.parallel
    resource_tasks = validated_resource_tasks(graph, frontier, parallel)
    require_claim_tasks(claim, frontier.pending_tasks)
    node_input = effective_node_input(graph, request.state, request.node_input)
    if resource_tasks:
        if parallel is None:
            raise ResultCollectionError("resource execution requires a committed parallel snapshot")
        results = await execute_resource_waves(
            graph,
            frontier,
            parallel,
            resource_tasks,
            node_input,
            request.nested_results,
        )
    elif frontier.nested_runs:
        raise ResultCollectionError("executor cannot retain a lease while waiting for nested graphs")
    else:
        results = await execute_tasks(graph, frontier.pending_tasks, node_input, request.nested_results)
    combined = tuple(sorted(results, key=lambda result: result.task.sort_key))
    transition = settle_tasks(graph, frontier.snapshot, frontier.tasks, combined)
    return ExecutedSuperstep(combined, project_graph_command(transition))


__all__ = ["execute_claimed_superstep", "prepare_superstep"]
