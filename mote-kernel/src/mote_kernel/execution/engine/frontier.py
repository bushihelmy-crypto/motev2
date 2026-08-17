"""Strict preparation of one committed frontier and its child projections."""

from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.snapshot_guard import require_snapshot_matches_graph
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph.definition import NestedGraphNodeDefinition
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import _node_output_from_view
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import (
    ActiveChild,
    CompletedChild,
    MissingChild,
    PreparedNestedRun,
    TaskFailure,
    TaskResult,
    TaskSuccess,
)
from mote_kernel.state.graph_state import (
    GraphRunStatus,
    ParentGraphActivation,
    child_graph_run_id,
    validate_graph_run_state,
)

GraphValueT = TypeVar("GraphValueT")


@dataclass(frozen=True, slots=True)
class FrontierPreparation(Generic[GraphValueT]):
    tasks: tuple[GraphTask, ...]
    executable_definitions: tuple[tuple[GraphTask, CallableNodeDefinition[GraphValueT]], ...]
    nested_results: tuple[TaskResult[GraphValueT], ...]
    missing_children: tuple[PreparedNestedRun[GraphValueT], ...]
    active_children: tuple[ActiveChild, ...]


def _activation(task: GraphTask) -> ParentGraphActivation:
    return ParentGraphActivation(task.run_id, task.superstep, task.node_id)


def prepare_frontier(
    graph: CompiledGraph[GraphValueT], request: StepRequest[GraphValueT]
) -> FrontierPreparation[GraphValueT]:
    tasks = plan_tasks(graph, request.state, request.limits)
    nested_tasks = tuple(task for task in tasks if task.node_id in graph.transition.nested_node_ids)
    expected = tuple(_activation(task) for task in nested_tasks)
    received = tuple(projection.parent for projection in request.child_projections)
    if received != expected:
        raise ResultCollectionError("child projections must exactly and canonically cover pending nested activations")

    missing: list[PreparedNestedRun[GraphValueT]] = []
    active: list[ActiveChild] = []
    nested_results: list[TaskResult[GraphValueT]] = []
    task_by_parent = {
        _activation(task): (task, definition)
        for task in nested_tasks
        if isinstance(definition := graph.nodes[task.node_id], NestedGraphNodeDefinition)
    }
    for projection in request.child_projections:
        parent = projection.parent
        task, definition = task_by_parent[parent]
        child_graph = graph.nested_graphs[definition.node_id]
        expected_run_id = child_graph_run_id(parent.run_id, parent.superstep, parent.node_id)
        if isinstance(projection, MissingChild):
            missing.append(
                PreparedNestedRun(
                    parent,
                    child_graph,
                    project_start_graph_command(child_graph, expected_run_id, parent),
                )
            )
            continue
        child = projection.child_state
        validate_graph_run_state(child)
        if (
            child.run_id != expected_run_id
            or child.parent != parent
            or child.definition_id != child_graph.definition_id
            or child.definition_version != child_graph.version
        ):
            raise ResultCollectionError("child projection does not match its parent activation or definition")
        require_snapshot_matches_graph(child_graph, child)
        if isinstance(projection, ActiveChild):
            if child.status is not GraphRunStatus.RUNNING:
                raise ResultCollectionError("active child requires a running child state")
            active.append(projection)
        elif isinstance(projection, CompletedChild):
            if child.status is not GraphRunStatus.COMPLETED:
                raise ResultCollectionError("completed child requires a completed child state")
            declarations = tuple((item.name, item.descriptor) for item in graph.outcomes[task.node_id].outputs.entries)
            nested_results.append(TaskSuccess(task, _node_output_from_view(projection.output, declarations), None))
        else:
            if child.status is not GraphRunStatus.ABORTED or child.abort is None:
                raise ResultCollectionError("aborted child requires an aborted child state")
            nested_results.append(TaskFailure(task, child.abort.reason))
    executable: list[tuple[GraphTask, CallableNodeDefinition[GraphValueT]]] = []
    for task in tasks:
        definition = graph.nodes[task.node_id]
        if task.node_id in graph.transition.callable_node_ids and isinstance(definition, CallableNodeDefinition):
            executable.append((task, definition))
    return FrontierPreparation(tasks, tuple(executable), tuple(nested_results), tuple(missing), tuple(active))


__all__ = ["FrontierPreparation", "prepare_frontier"]
