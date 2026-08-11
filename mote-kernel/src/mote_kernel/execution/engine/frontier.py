"""Pure preparation of one committed frontier before admission or execution."""

from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.resolution_input import require_resolution_binding
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import CompiledGraph, NestedGraphNodeDefinition, NodeDefinition, compile_graph
from mote_kernel.execution.graph_run import project_execution_snapshot, project_start_graph_command
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import NestedTaskFailure, PreparedNestedRun
from mote_kernel.execution.snapshot import ExecutionSnapshot
from mote_kernel.state.graph_state import GraphRunId, GraphRunStatus, GraphTaskId, ParentGraphTask
from mote_kernel.state.graph_state.reducer import validate_graph_run_state

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class PreparedSuperstep(Generic[InputT, OutputT]):
    """Validated frontier facts shared by claim and settlement stages."""

    snapshot: ExecutionSnapshot
    tasks: tuple[GraphTask, ...]
    pending_tasks: tuple[GraphTask, ...]
    nested_runs: tuple[PreparedNestedRun[InputT, OutputT], ...]
    executable_definitions: tuple[tuple[GraphTask, NodeDefinition[InputT, OutputT]], ...]


def _child_run_id(parent_run_id: str, task_id: str) -> GraphRunId:
    return GraphRunId(f"{len(parent_run_id)}:{parent_run_id}:{len(task_id)}:{task_id}")


def prepare_frontier(
    graph: CompiledGraph[InputT, OutputT], request: StepRequest[InputT, OutputT]
) -> PreparedSuperstep[InputT, OutputT]:
    """Validate recovered inputs and materialize executable and nested frontier parts."""

    snapshot = project_execution_snapshot(request.state)
    tasks = plan_tasks(graph, snapshot, request.limits)
    planned_by_id = {task.task_id: task for task in tasks}
    pending_tasks = tasks
    definition_by_task = {task.task_id: graph.nodes[task.node_id] for task in pending_tasks}
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
        validate_graph_run_state(child_state)
        require_resolution_binding(compile_graph(definition.graph), child_state)

    nested_runs: list[PreparedNestedRun[InputT, OutputT]] = []
    for task_id, definition in nested_definitions.items():
        if task_id in nested_results:
            continue
        task = planned_by_id[task_id]
        child_graph = compile_graph(definition.graph)
        nested_runs.append(
            PreparedNestedRun(
                task,
                child_graph,
                project_start_graph_command(
                    child_graph,
                    _child_run_id(request.state.run_id, task.task_id),
                    ParentGraphTask(request.state.run_id, GraphTaskId(task.task_id)),
                ),
            )
        )
    executable_definitions: list[tuple[GraphTask, NodeDefinition[InputT, OutputT]]] = []
    for task in pending_tasks:
        definition = definition_by_task[task.task_id]
        if isinstance(definition, NodeDefinition):
            executable_definitions.append((task, definition))
    return PreparedSuperstep(
        snapshot,
        tasks,
        pending_tasks,
        tuple(sorted(nested_runs, key=lambda run: run.parent_task.sort_key)),
        tuple(executable_definitions),
    )


__all__ = ["PreparedSuperstep", "prepare_frontier"]
