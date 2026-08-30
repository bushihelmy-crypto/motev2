"""Strict preparation of one committed frontier and its child projections."""

from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import _node_output_from_view
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import (
    ActiveChild,
    CompletedChild,
    MissingChild,
    TaskFailure,
    TaskResult,
    TaskSuccess,
)
from mote_kernel.state.graph_state import ParentGraphActivation

GraphValueT = TypeVar("GraphValueT")


@dataclass(frozen=True, slots=True)
class FrontierPreparation(Generic[GraphValueT]):
    tasks: tuple[GraphTask, ...]
    executable_definitions: tuple[tuple[GraphTask, CallableNodeDefinition[GraphValueT]], ...]
    nested_results: tuple[TaskResult[GraphValueT], ...]
    missing_children: tuple[MissingChild, ...]
    active_children: tuple[ActiveChild, ...]


def _activation(task: GraphTask) -> ParentGraphActivation:
    return ParentGraphActivation(task.run_id, task.superstep, task.node_id)


def prepare_frontier(
    graph: CompiledGraph[GraphValueT], request: StepRequest[GraphValueT]
) -> FrontierPreparation[GraphValueT]:
    tasks = plan_tasks(graph, request.state, request.limits)
    nested_tasks = tuple(task for task in tasks if task.node_id in graph.nested_graphs)
    expected = tuple(_activation(task) for task in nested_tasks)
    received = tuple(projection.parent for projection in request.child_projections)
    if received != expected:
        raise ResultCollectionError("child projections must exactly and canonically cover pending nested activations")

    missing: list[MissingChild] = []
    active: list[ActiveChild] = []
    nested_results: list[TaskResult[GraphValueT]] = []
    task_by_parent = {_activation(task): task for task in nested_tasks}
    for projection in request.child_projections:
        parent = projection.parent
        task = task_by_parent[parent]
        if isinstance(projection, MissingChild):
            missing.append(projection)
            continue
        if isinstance(projection, ActiveChild):
            active.append(projection)
        elif isinstance(projection, CompletedChild):
            declarations = tuple(
                (item.name, item.descriptor)
                for item in graph.transition.publications[task.node_id].declarations.entries
            )
            nested_results.append(TaskSuccess(task, _node_output_from_view(projection.output, declarations), None))
        else:
            nested_results.append(TaskFailure(task, projection.reason))
    executable: list[tuple[GraphTask, CallableNodeDefinition[GraphValueT]]] = []
    for task in tasks:
        definition = graph.nodes[task.node_id]
        if isinstance(definition, CallableNodeDefinition):
            executable.append((task, definition))
    return FrontierPreparation(tasks, tuple(executable), tuple(nested_results), tuple(missing), tuple(active))


__all__ = ["FrontierPreparation", "prepare_frontier"]
