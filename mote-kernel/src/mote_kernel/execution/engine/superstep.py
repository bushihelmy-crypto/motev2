"""The plan, execute, collect, transition superstep."""

from typing import TypeVar

from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.scheduler import execute_tasks
from mote_kernel.execution.engine.settlement import settle_tasks
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import NestedGraphNodeDefinition, compile_graph
from mote_kernel.execution.graph_run import project_execution_snapshot, project_graph_command
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import (
    ExecutedSuperstep,
    NestedTaskFailure,
    PreparedNestedRun,
    PreparedNestedRuns,
    StepResult,
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
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def _child_run_id(parent_run_id: str, task_id: str) -> GraphRunId:
    return GraphRunId(f"{len(parent_run_id)}:{parent_run_id}:{len(task_id)}:{task_id}")


def execute_superstep(request: StepRequest[InputT, OutputT]) -> StepResult[InputT, OutputT]:
    """Execute one frontier and propose, but never commit, its state command."""

    snapshot = project_execution_snapshot(request.state)
    tasks = plan_tasks(request.graph, snapshot, request.limits)
    nested_results = {result.task_id: result for result in request.nested_results}
    if len(nested_results) != len(request.nested_results):
        raise ResultCollectionError("nested task results must have unique task identities")
    nested_definitions = {
        task.task_id: definition
        for task in tasks
        if isinstance(definition := request.graph.nodes[task.node_id], NestedGraphNodeDefinition)
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
    for task in tasks:
        definition = request.graph.nodes[task.node_id]
        if isinstance(definition, NestedGraphNodeDefinition) and task.task_id not in nested_results:
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
    if pending_nested:
        return PreparedNestedRuns(tuple(pending_nested))
    results = execute_tasks(request.graph, tasks, request.node_input, request.nested_results)
    transition = settle_tasks(request.graph, snapshot, tasks, results)
    return ExecutedSuperstep(results, project_graph_command(transition))


__all__ = ["execute_superstep"]
