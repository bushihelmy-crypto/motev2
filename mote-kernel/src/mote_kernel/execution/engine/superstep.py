"""The plan, execute, collect, transition superstep."""

from typing import TypeVar

from mote_kernel.execution.engine.admission import admit_tasks
from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.scheduler import execute_tasks
from mote_kernel.execution.engine.settlement import settle_tasks
from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import NestedGraphNodeDefinition, NodeDefinition, compile_graph
from mote_kernel.execution.graph_run import project_execution_snapshot, project_graph_command
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import (
    ExecutedFrontierBatch,
    ExecutedSuperstep,
    NestedTaskFailure,
    PreparedFrontier,
    PreparedNestedRun,
    PreparedResourceAdmission,
    StepResult,
    TaskResult,
)
from mote_kernel.parallel import (
    ParallelSnapshot,
    ParticipantId,
    ReleaseResources,
    ResourceLock,
    reduce_parallel,
)
from mote_kernel.state.graph_state import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRunId,
    GraphRunStatus,
    GraphTaskId,
    ParentGraphTask,
    StartGraphRun,
    UpdateGraphParallel,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def _child_run_id(parent_run_id: str, task_id: str) -> GraphRunId:
    return GraphRunId(f"{len(parent_run_id)}:{parent_run_id}:{len(task_id)}:{task_id}")


def _empty_parallel_snapshot(request: StepRequest[InputT, OutputT]) -> ParallelSnapshot:
    return ParallelSnapshot(tuple(ResourceLock(resource_id) for resource_id in request.graph.resource_order))


def _require_parallel_matches_graph(request: StepRequest[InputT, OutputT], snapshot: ParallelSnapshot) -> None:
    resource_ids = tuple(resource.resource_id for resource in snapshot.resources)
    if resource_ids != request.graph.resource_order:
        raise ResultCollectionError("committed parallel snapshot does not match graph resource order")


async def execute_superstep(request: StepRequest[InputT, OutputT]) -> StepResult[InputT, OutputT]:
    """Execute one frontier and propose, but never commit, its state command."""

    snapshot = project_execution_snapshot(request.state)
    tasks = plan_tasks(request.graph, snapshot, request.limits)
    if request.state.parallel is not None:
        _require_parallel_matches_graph(request, request.state.parallel)
    planned_by_id = {task.task_id: task for task in tasks}
    settled_by_task: dict[TaskId, TaskResult[OutputT]] = {}
    for result in request.settled_results:
        planned = planned_by_id.get(result.task.task_id)
        if planned is None or planned != result.task or result.task.task_id in settled_by_task:
            raise ResultCollectionError("settled results must exactly match unique planned tasks")
        settled_by_task[result.task.task_id] = result
    committed_settled = frozenset(TaskId(task_id) for task_id in request.state.settled_tasks)
    if frozenset(settled_by_task) != committed_settled:
        raise ResultCollectionError("settled result values must exactly cover committed settled tasks")
    pending_tasks = tuple(task for task in tasks if task.task_id not in committed_settled)
    definition_by_task = {task.task_id: request.graph.nodes[task.node_id] for task in pending_tasks}
    nested_results = {result.task_id: result for result in request.nested_results}
    if len(nested_results) != len(request.nested_results):
        raise ResultCollectionError("nested task results must have unique task identities")
    nested_definitions = {
        task.task_id: definition
        for task in pending_tasks
        if isinstance(definition := definition_by_task[task.task_id], NestedGraphNodeDefinition)
    }
    if not set(nested_results) <= set(nested_definitions):
        raise ResultCollectionError("received a nested result for an unknown parent task")
    for task_id, result in nested_results.items():
        child_state = result.child_state
        definition = nested_definitions[task_id]
        if child_state.run_id != _child_run_id(request.state.run_id, task_id):
            raise ResultCollectionError("nested task result does not match its deterministic child run identity")
        expected_parent = ParentGraphTask(request.state.run_id, GraphTaskId(task_id))
        if child_state.parent != expected_parent:
            raise ResultCollectionError("nested task result does not match its committed parent task")
        if (
            child_state.definition_id != definition.graph.definition_id
            or child_state.definition_version != definition.graph.version
        ):
            raise ResultCollectionError("nested task result does not match its child graph definition")
        if isinstance(result, NestedTaskFailure):
            if child_state.status is not GraphRunStatus.FAILED or child_state.failure != result.failure:
                raise ResultCollectionError("nested failure does not match committed failed child state")
        elif child_state.status is not GraphRunStatus.COMPLETED:
            raise ResultCollectionError("nested success requires a committed completed child state")

    pending_nested: list[PreparedNestedRun[InputT, OutputT]] = []
    for task_id, definition in nested_definitions.items():
        if task_id in nested_results:
            continue
        task = planned_by_id[task_id]
        child_graph = compile_graph(definition.graph)
        pending_nested.append(
            PreparedNestedRun(
                task,
                child_graph,
                StartGraphRun(
                    _child_run_id(request.state.run_id, task.task_id),
                    GraphDefinitionId(child_graph.definition_id),
                    GraphDefinitionVersion(child_graph.version),
                    tuple(GraphNodeId(node_id) for node_id in child_graph.entries),
                    ParentGraphTask(request.state.run_id, GraphTaskId(task.task_id)),
                ),
            )
        )
    prepared_nested = tuple(sorted(pending_nested, key=lambda run: run.parent_task.sort_key))

    executable_definitions: list[tuple[GraphTask, NodeDefinition[InputT, OutputT]]] = []
    for task in pending_tasks:
        definition = definition_by_task[task.task_id]
        if isinstance(definition, NodeDefinition):
            executable_definitions.append((task, definition))
    resource_participants = frozenset(
        ParticipantId(task.task_id) for task, definition in executable_definitions if definition.resources
    )
    if (
        request.state.parallel is not None
        and not frozenset(acquisition.participant_id for acquisition in request.state.parallel.acquisitions)
        <= resource_participants
    ):
        raise ResultCollectionError(
            "committed parallel snapshot contains an acquisition outside pending resource tasks"
        )
    has_resource_tasks = bool(resource_participants)
    if has_resource_tasks:
        parallel_snapshot = request.state.parallel or _empty_parallel_snapshot(request)
        admission = admit_tasks(
            request.graph,
            tuple(task for task, _definition in executable_definitions),
            parallel_snapshot,
        )
        acquisition_by_participant = {
            acquisition.participant_id: acquisition for acquisition in parallel_snapshot.acquisitions
        }
        committed_resource_tasks = tuple(
            task
            for task, definition in executable_definitions
            if definition.resources
            and (acquisition := acquisition_by_participant.get(ParticipantId(task.task_id))) is not None
            and acquisition.admitted
        )
        if admission.snapshot != parallel_snapshot:
            return PreparedFrontier(
                PreparedResourceAdmission(
                    admission.admitted,
                    admission.waiting,
                    UpdateGraphParallel(request.state.superstep, request.state.parallel, admission.snapshot),
                ),
                prepared_nested,
            )
        committed_resource_ids = frozenset(task.task_id for task in committed_resource_tasks)
        executable_tasks = tuple(
            task
            for task, definition in executable_definitions
            if not definition.resources or task.task_id in committed_resource_ids
        )
        batch_results = await execute_tasks(request.graph, executable_tasks, request.node_input)
        released = parallel_snapshot
        for task in reversed(committed_resource_tasks):
            released = reduce_parallel(released, ReleaseResources(ParticipantId(task.task_id)))
        combined = tuple(sorted((*request.settled_results, *batch_results), key=lambda result: result.task.sort_key))
        if len(combined) < len(tasks):
            return ExecutedFrontierBatch(
                batch_results,
                UpdateGraphParallel(
                    request.state.superstep,
                    request.state.parallel,
                    released,
                    tuple(GraphTaskId(result.task.task_id) for result in batch_results),
                ),
            )
        transition = settle_tasks(request.graph, snapshot, tasks, combined)
        return ExecutedSuperstep(combined, project_graph_command(transition))
    if prepared_nested:
        return PreparedFrontier(None, prepared_nested)
    results = await execute_tasks(request.graph, pending_tasks, request.node_input, request.nested_results)
    combined = tuple(sorted((*request.settled_results, *results), key=lambda result: result.task.sort_key))
    transition = settle_tasks(request.graph, snapshot, tasks, combined)
    return ExecutedSuperstep(combined, project_graph_command(transition))


__all__ = ["execute_superstep"]
