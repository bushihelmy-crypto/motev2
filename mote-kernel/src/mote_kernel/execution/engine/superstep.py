"""Explicit prepare, claim, execute, and settle orchestration for one superstep."""

from typing import TypeVar

from mote_kernel.execution.claim import ExecutionClaimOwner, PreparedExecutionClaim
from mote_kernel.execution.engine.admission import admit_tasks
from mote_kernel.execution.engine.claim_stage import interrupt_generation, prepare_claim, require_claim_tasks
from mote_kernel.execution.engine.frontier import PreparedSuperstep, prepare_frontier
from mote_kernel.execution.engine.resolution_input import effective_node_input
from mote_kernel.execution.engine.scheduler import execute_tasks
from mote_kernel.execution.engine.settlement import settle_tasks
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.graph_run import project_graph_command
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import (
    ExecutedSuperstep,
    PreparedFrontier,
    PreparedResourceAdmission,
    TaskResult,
)
from mote_kernel.parallel import ParallelSnapshot, ParticipantId, ReleaseResources, ResourceLock, reduce_parallel
from mote_kernel.state.graph_state import UpdateGraphParallel

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def _parallel_snapshot(graph: CompiledGraph[InputT, OutputT]) -> ParallelSnapshot:
    return ParallelSnapshot(tuple(ResourceLock(resource_id) for resource_id in graph.resource_order))


def _require_parallel_matches_graph(graph: CompiledGraph[InputT, OutputT], snapshot: ParallelSnapshot) -> None:
    if tuple(resource.resource_id for resource in snapshot.resources) != graph.resource_order:
        raise ResultCollectionError("committed parallel snapshot does not match graph resource order")


def _resource_tasks(frontier: PreparedSuperstep[InputT, OutputT]) -> frozenset[ParticipantId]:
    return frozenset(
        ParticipantId(task.task_id) for task, definition in frontier.executable_definitions if definition.resources
    )


def _validated_resource_tasks(
    graph: CompiledGraph[InputT, OutputT],
    frontier: PreparedSuperstep[InputT, OutputT],
    parallel: ParallelSnapshot | None,
) -> frozenset[ParticipantId]:
    if parallel is not None:
        _require_parallel_matches_graph(graph, parallel)
    resource_tasks = _resource_tasks(frontier)
    if (
        parallel is not None
        and not frozenset(acquisition.participant_id for acquisition in parallel.acquisitions) <= resource_tasks
    ):
        raise ResultCollectionError(
            "committed parallel snapshot contains an acquisition outside pending resource tasks"
        )
    return resource_tasks


def _committed_resource_batch(
    frontier: PreparedSuperstep[InputT, OutputT], parallel: ParallelSnapshot
) -> tuple[GraphTask, ...]:
    acquisitions = {acquisition.participant_id: acquisition for acquisition in parallel.acquisitions}
    return tuple(
        task
        for task, definition in frontier.executable_definitions
        if definition.resources
        and (acquisition := acquisitions.get(ParticipantId(task.task_id))) is not None
        and acquisition.admitted
    )


async def prepare_superstep(
    owner: ExecutionClaimOwner,
    graph: CompiledGraph[InputT, OutputT],
    request: StepRequest[InputT, OutputT],
) -> PreparedFrontier[InputT, OutputT]:
    """Prepare nested runs, resource admission, or one linear execution claim."""

    frontier = prepare_frontier(graph, request)
    parallel = request.state.parallel
    resource_tasks = _validated_resource_tasks(graph, frontier, parallel)
    if not frontier.pending_tasks:
        return PreparedFrontier(None, ())
    if frontier.nested_runs:
        return PreparedFrontier(None, frontier.nested_runs)
    if resource_tasks:
        current = parallel or _parallel_snapshot(graph)
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
    resource_tasks = _validated_resource_tasks(graph, frontier, parallel)
    require_claim_tasks(claim, frontier.pending_tasks)
    node_input = effective_node_input(graph, request.state, request.node_input)
    results: tuple[TaskResult[OutputT], ...]
    if resource_tasks:
        if parallel is None:
            raise ResultCollectionError("resource execution requires a committed parallel snapshot")
        current = parallel
        remaining = set(resource_tasks)
        nonresource = tuple(
            task for task in frontier.pending_tasks if ParticipantId(task.task_id) not in resource_tasks
        )
        collected: list[TaskResult[OutputT]] = []
        first_wave = True
        nested_by_id = {result.task_id: result for result in request.nested_results}
        while remaining:
            committed = _committed_resource_batch(frontier, current)
            committed = tuple(task for task in committed if ParticipantId(task.task_id) in remaining)
            if not committed:
                raise ResultCollectionError("resource scheduler cannot advance a committed acquisition")
            wave = (*nonresource, *committed) if first_wave else committed
            nested_results = tuple(nested_by_id[task.task_id] for task in wave if task.task_id in nested_by_id)
            collected.extend(await execute_tasks(graph, wave, node_input, nested_results))
            for task in reversed(committed):
                current = reduce_parallel(current, ReleaseResources(ParticipantId(task.task_id)))
                remaining.remove(ParticipantId(task.task_id))
            first_wave = False
        results = tuple(collected)
    elif frontier.nested_runs:
        raise ResultCollectionError("executor cannot retain a lease while waiting for nested graphs")
    else:
        results = await execute_tasks(graph, frontier.pending_tasks, node_input, request.nested_results)
    combined = tuple(sorted(results, key=lambda result: result.task.sort_key))
    transition = settle_tasks(graph, frontier.snapshot, frontier.tasks, combined)
    return ExecutedSuperstep(combined, project_graph_command(transition))


__all__ = ["execute_claimed_superstep", "prepare_superstep"]
