"""Strict preparation of one committed frontier and its child projections."""

from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.resume_input import materialize_node_input
from mote_kernel.execution.engine.task import ExecutableTask, GraphTask
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
    request: StepRequest[GraphValueT]
    tasks: tuple[GraphTask, ...]
    executables: tuple[ExecutableTask[GraphValueT], ...]
    nested_results: tuple[TaskResult[GraphValueT], ...]
    missing_children: tuple[MissingChild, ...]
    active_children: tuple[ActiveChild, ...]


def prepare_frontier(
    graph: CompiledGraph[GraphValueT], request: StepRequest[GraphValueT]
) -> FrontierPreparation[GraphValueT]:
    tasks = plan_tasks(graph, request.state, request.limits)
    nested_tasks = tuple(task for task in tasks if task.node_id in graph.nested_graphs)
    expected = tuple(ParentGraphActivation(task.run_id, task.superstep, task.node_id) for task in nested_tasks)
    received = tuple(projection.parent for projection in request.child_projections)
    if received != expected:
        raise ResultCollectionError("child projections must exactly and canonically cover pending nested activations")

    missing: list[MissingChild] = []
    active: list[ActiveChild] = []
    nested_results: list[TaskResult[GraphValueT]] = []
    for task, projection in zip(nested_tasks, request.child_projections, strict=True):
        if isinstance(projection, MissingChild):
            missing.append(projection)
            continue
        if isinstance(projection, ActiveChild):
            active.append(projection)
        elif isinstance(projection, CompletedChild):
            declarations = graph.transition.publications[task.node_id].declarations
            nested_results.append(TaskSuccess(task, _node_output_from_view(projection.output, declarations), None))
        else:
            nested_results.append(TaskFailure(task, projection.reason))
    executables = (
        tuple(
            ExecutableTask(
                task,
                materialize_node_input(graph, request.state, request.scope_run, request.frames, task.node_id),
            )
            for task in tasks
            if isinstance(graph.nodes[task.node_id], CallableNodeDefinition)
        )
        if not missing and not active
        else ()
    )
    return FrontierPreparation(
        request,
        tasks,
        executables,
        tuple(nested_results),
        tuple(missing),
        tuple(active),
    )


__all__ = ["FrontierPreparation", "prepare_frontier"]
