"""Committed resource admission and wave execution for one frontier."""

from typing import TypeVar

from mote_kernel.execution.engine.frontier import PreparedSuperstep
from mote_kernel.execution.engine.scheduler import execute_tasks
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.result import NestedTaskResult, TaskResult
from mote_kernel.parallel import ParallelSnapshot, ParticipantId, ReleaseResources, ResourceLock, reduce_parallel

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def initial_parallel_snapshot(graph: CompiledGraph[InputT, OutputT]) -> ParallelSnapshot:
    """Create the empty lock table fixed by compiled resource order."""

    return ParallelSnapshot(tuple(ResourceLock(resource_id) for resource_id in graph.resource_order))


def validated_resource_tasks(
    graph: CompiledGraph[InputT, OutputT],
    frontier: PreparedSuperstep[InputT, OutputT],
    parallel: ParallelSnapshot | None,
) -> frozenset[ParticipantId]:
    """Return resource task identities after validating committed admission ownership."""

    if parallel is not None and tuple(resource.resource_id for resource in parallel.resources) != graph.resource_order:
        raise ResultCollectionError("committed parallel snapshot does not match graph resource order")
    resource_tasks = frozenset(
        ParticipantId(task.task_id) for task, definition in frontier.executable_definitions if definition.resources
    )
    if (
        parallel is not None
        and not frozenset(acquisition.participant_id for acquisition in parallel.acquisitions) <= resource_tasks
    ):
        raise ResultCollectionError(
            "committed parallel snapshot contains an acquisition outside pending resource tasks"
        )
    return resource_tasks


def _committed_resource_batch(
    frontier: PreparedSuperstep[InputT, OutputT],
    parallel: ParallelSnapshot,
) -> tuple[GraphTask, ...]:
    acquisitions = {acquisition.participant_id: acquisition for acquisition in parallel.acquisitions}
    return tuple(
        task
        for task, definition in frontier.executable_definitions
        if definition.resources
        and (acquisition := acquisitions.get(ParticipantId(task.task_id))) is not None
        and acquisition.admitted
    )


async def execute_resource_waves(
    graph: CompiledGraph[InputT, OutputT],
    frontier: PreparedSuperstep[InputT, OutputT],
    parallel: ParallelSnapshot,
    resource_tasks: frozenset[ParticipantId],
    node_input: InputT,
    nested_results: tuple[NestedTaskResult[OutputT], ...],
) -> tuple[TaskResult[OutputT], ...]:
    """Execute every admitted resource wave under one committed execution claim."""

    current = parallel
    remaining = set(resource_tasks)
    nonresource = tuple(task for task in frontier.pending_tasks if ParticipantId(task.task_id) not in resource_tasks)
    collected: list[TaskResult[OutputT]] = []
    first_wave = True
    nested_by_id = {result.task_id: result for result in nested_results}
    while remaining:
        committed = tuple(
            task for task in _committed_resource_batch(frontier, current) if ParticipantId(task.task_id) in remaining
        )
        if not committed:
            raise ResultCollectionError("resource scheduler cannot advance a committed acquisition")
        wave = (*nonresource, *committed) if first_wave else committed
        wave_nested_results = tuple(nested_by_id[task.task_id] for task in wave if task.task_id in nested_by_id)
        collected.extend(await execute_tasks(graph, wave, node_input, wave_nested_results))
        for task in reversed(committed):
            current = reduce_parallel(current, ReleaseResources(ParticipantId(task.task_id)))
            remaining.remove(ParticipantId(task.task_id))
        first_wave = False
    return tuple(collected)


__all__: list[str] = []
